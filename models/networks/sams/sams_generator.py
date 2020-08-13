import argparse
import logging
from typing import List, Dict, Union
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
        # if n_frames_total is 1, then we pass in a 0 for the prev frame anyway
        num_prev_frames = max(hparams.n_frames_total - 1, 1)
        self.in_channels = in_channels = TryonDataset.RGB_CHANNELS * num_prev_frames
        out_channels = TryonDataset.RGB_CHANNELS

        NGF_OUTER = out_feat = int(hparams.ngf_base ** hparams.ngf_power_start)
        NGF_INNER = int(hparams.ngf_base ** hparams.ngf_power_end)

        # ----- ENCODE --------
        enc_lab_c = getattr(TryonDataset, f"{hparams.encoder_input.upper()}_CHANNELS")
        kwargs = {
            "norm_G": hparams.norm_G,  # prev frames is n_frames_total - 1
            "label_channels": enc_lab_c * num_prev_frames,
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
        prev_n_frames_G: Union[Tensor, None],
        prev_n_labelmaps: Union[Tensor, None],
        current_labelmap_dict: Dict[str, Tensor],
    ):
        """
        Args:
            prev_n_frames_G (Tensor shape b x n x c x h x w): previous synthesized
             frames. If None, then n_frames_total must == 1.
            prev_n_labelmaps (Tensor shape b x n x c x h x w): labelmap for the previous
             frames. If None, then n_frames_total must == 1.
            current_labelmap_dict: segmentation maps for the current frame

        Returns: synthesized output for the current frame
        """
        # prepare data, combine frames onto the channels dim
        if self.hparams.n_frames_total > 1:
            b, n, c, h, w = prev_n_frames_G.shape
            prev_n_frames_G = prev_n_frames_G.view(b, -1, h, w)
            b, n, c, h, w = prev_n_labelmaps.shape
            prev_n_labelmaps = prev_n_labelmaps.view(b, -1, h, w)
        else:
            # set to Zeros
            reference = list(current_labelmap_dict.values())[0]
            b, _, h, w = reference.shape  # only 4D because dataset not adding Nframes
            prev_n_frames_G = torch.zeros(b, self.in_channels, h, w).type_as(
                reference
            )
            prev_n_labelmaps = torch.zeros(b, self.in_channels, h, w).type_as(reference)

        x = prev_n_frames_G

        # forward
        logger.debug(f"{x.shape=}")
        for encoder in self.encode_layers:
            if isinstance(encoder, AnySpadeResBlock):
                x = encoder(x, prev_n_labelmaps)
            else:
                x = encoder(x)
            logger.debug(f"{x.shape=}")

        for middle in self.middle_layers:
            x = middle(x, current_labelmap_dict)
            logger.debug(f"{x.shape=}")

        for decoder in self.decode_layers:
            if isinstance(decoder, AnySpadeResBlock):
                x = decoder(x, current_labelmap_dict)
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