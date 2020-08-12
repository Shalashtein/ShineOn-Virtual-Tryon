import argparse
import logging
from typing import List, Dict
import sys

import torch
from torch import Tensor
from torch import nn

from datasets.tryon_dataset import TryonDataset
from models.networks import BaseNetwork
from .attentive_multispade import AttentiveMultiSpade
from .multispade import MultiSpade
from .spade import AnySpadeResBlock, SPADE

logger = logging.getLogger("logger")


class SamsGenerator(BaseNetwork):
    """
    The generator for Self-Attentive Multispade GAN model.
    The network is Encoder-Decoder style, but with Self-Attentive Multispade layers.

    Encoder:
        The input passed into the base of the encoder is the past n-frames. Resolution
        increases while feature maps decrease.

        Unlike the Middle and Decoder parts, the Encoder is made with simple SPADE
        layers, not multi-spade nor SAMS. Therefore the encoder only takes a single
        annotation map to pass to SPADE.

    Middle:
        The Middle is made of SAMS blocks and therefore uses all the annotation maps.
        It preserves the number of channels and resolution at that stage.

    Decoder:
        The Decoder is also made of SAMS blocks. Resolution increases while feature maps
        decrease.

    See `SamsGenerator.modify_commandline_options()` for controlling the size of the
    network.
    """

    @classmethod
    def modify_commandline_options(cls, parser: argparse.ArgumentParser, is_train):
        parser = BaseNetwork.modify_commandline_options(parser, is_train)
        parser.set_defaults(self_attn=True)
        parser.add_argument("--norm_G", default="spectralspadesyncbatch3x3")
        parser.add_argument(
            "--ngf_base",
            type=int,
            default=2,
            help="Control the size of the network. ngf_base ** pow",
        )
        parser.add_argument(
            "--ngf_power_start",
            type=int,
            default=6,
            help="number of features at the outer ends = ngf_base ** start; "
            "decrease for less features",
        )
        parser.add_argument(
            "--ngf_power_end",
            type=int,
            default=10,
            help="INCLUSIVE! number of features in the middle of the network "
            "= ngf_base ** end; decrease for less features",
        )
        parser.add_argument(
            "--ngf_power_step",
            type=int,
            default=1,
            help="increment the power this much between layers until >= ngf_power_end; "
            "increase for less layers. Total layers is: "
            "(ngf_power_end - ngf_power_start + 1) // ngf_power_step",
        )
        parser.add_argument(
            "--num_middle",
            type=int,
            default=3,
            help="Number of channel-preserving layers between the encoder and decoder",
        )
        if "--ngf" in sys.argv:
            logger.warning(
                "SamsGenerator does NOT use --ngf. "
                "Use --ngf_base, --ngf_power_start, --ngf_power_end, --ngf_power_step, "
                "and --num_middle to control the architecture."
            )
        return parser

    def __init__(self, hparams):
        super().__init__()
        assert hparams.ngf_base > 1, f"{hparams.ngf_base}"
        assert hparams.ngf_power_end >= 1, f"{hparams.ngf_power_end=}"

        self.hparams = hparams
        self.inputs = hparams.person_inputs + hparams.cloth_inputs
        in_channels = TryonDataset.RGB_CHANNELS * hparams.n_frames
        out_channels = TryonDataset.RGB_CHANNELS

        NGF_OUTER = out_feat = int(hparams.ngf_base ** hparams.ngf_power_start)
        NGF_INNER = int(hparams.ngf_base ** hparams.ngf_power_end)

        # ----- ENCODE --------
        enc_lab_c = getattr(TryonDataset, f"{hparams.encoder_input.upper()}_CHANNELS")
        kwargs = {
            "norm_G": hparams.norm_G,
            "label_channels": enc_lab_c * hparams.n_frames,
            "spade_class": SPADE,
        }
        self.encode_layers = [
            nn.Conv2d(
                in_channels=in_channels,
                out_channels=NGF_OUTER,
                kernel_size=3,
                padding=1,
            )
        ]
        for pow in range(
            hparams.ngf_power_start, hparams.ngf_power_end, hparams.ngf_power_step
        ):
            in_feat = int(hparams.ngf_base ** pow)
            out_feat = int(hparams.ngf_base ** (pow + hparams.ngf_power_step))
            self.encode_layers.extend(make_encode_block(in_feat, out_feat, **kwargs))
        # ensure we get to exactly ngf_base ** ngf_power_end
        self.encode_layers.extend(make_encode_block(out_feat, NGF_INNER, **kwargs))
        self.encode_layers = nn.ModuleList(self.encode_layers)

        # ------ MIDDLE ------
        kwargs = {
            "norm_G": hparams.norm_G,
            "label_channels": {
                inp: getattr(TryonDataset, f"{inp.upper()}_CHANNELS")
                for inp in sorted(self.inputs)
            },
            "spade_class": AttentiveMultiSpade if hparams.self_attn else MultiSpade,
        }
        self.middle_layers = nn.ModuleList(
            AnySpadeResBlock(NGF_INNER, NGF_INNER, **kwargs)
            for _ in range(hparams.num_middle)
        )

        # ----- DECODE --------
        self.decode_layers = nn.ModuleList()
        for pow in range(
            hparams.ngf_power_end, hparams.ngf_power_start, -hparams.ngf_power_step
        ):
            in_feat = int(hparams.ngf_base ** pow)
            out_feat = int(hparams.ngf_base ** (pow - hparams.ngf_power_step))
            self.decode_layers.extend(make_decode_block(in_feat, out_feat, **kwargs))
        self.decode_layers.extend(make_decode_block(out_feat, NGF_OUTER, **kwargs))
        self.decode_layers.append(
            nn.Conv2d(NGF_OUTER, out_channels, kernel_size=3, padding=1)
        )

    def forward(
        self,
        prev_synth_outputs: List[Tensor],
        prev_segmaps: List[Tensor],
        current_segmaps_dict: Dict[str, Tensor],
    ):
        """
        Args:
            prev_synth_outputs: previous synthesized frames
            prev_segmaps: segmaps for the previous frames
            current_segmaps_dict: segmentation maps for the current frame

        Returns: synthesized output for the current frame
        """
        # prepare
        prev_synth_outputs = (
            torch.cat(prev_synth_outputs, dim=1)
            if not isinstance(prev_synth_outputs, Tensor)
            else prev_synth_outputs
        )
        prev_segmaps = (
            torch.cat(prev_segmaps, dim=1)
            if not isinstance(prev_segmaps, Tensor)
            else prev_segmaps
        )
        x = prev_synth_outputs

        # forward
        logger.debug(f"{x.shape=}")
        for encoder in self.encode_layers:
            if isinstance(encoder, AnySpadeResBlock):
                x = encoder(x, prev_segmaps)
            else:
                x = encoder(x)
            logger.debug(f"{x.shape=}")

        for middle in self.middle_layers:
            x = middle(x, current_segmaps_dict)
            logger.debug(f"{x.shape=}")

        for decoder in self.decode_layers:
            if isinstance(decoder, AnySpadeResBlock):
                x = decoder(x, current_segmaps_dict)
            else:
                x = decoder(x)
            logger.debug(f"{x.shape=}")
        return x


def make_encode_block(in_feat, out_feat, **spade_kwargs):
    """ AnySpadeResBlock then downsample """
    return [
        AnySpadeResBlock(in_feat, out_feat, **spade_kwargs),
        # says "upsample", but we're actually shrinking resolution
        nn.Upsample(scale_factor=0.5),
    ]


def make_decode_block(in_feat, out_feat, **spade_kwargs):
    """ Upsample then AnySpadeResBlock """
    return [
        nn.Upsample(scale_factor=2),
        AnySpadeResBlock(in_feat, out_feat, **spade_kwargs),
    ]
