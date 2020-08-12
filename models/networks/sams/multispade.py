from typing import Dict, Callable

from torch import Tensor, nn

from models.networks.sams.spade import SPADE


class MultiSpade(SPADE):
    """ N sequential spades for the number of maps, from WC-Vid2Vid page 24 """

    DEFAULT_KEY = "default_key"

    def __init__(
        self,
        config_text: str,
        norm_nc: int,
        label_channels_dict: Dict[str, int],
        sort_fn: Callable = sorted,
    ):
        """
        Duck typing the original Spade class.
        Unclear if WC-Vid2Vid put a duplicate batchnorm layer before the first SPADE
        layer, because technically the first SPADE layer already batchnorms. Leaving
        w/out for now.
        Args:
            config_text: config_text for Spade
            norm_nc: number of channels through norm
            label_channels_dict: number of channels for the segmentation maps
            sort_fn: a callable on how to sort the dictionary by keys (
                i.e. the order layers are called). Default is natural sorting order 
                (alphabetical)
        """
        # skip super call of SPADE and go to nn.Module, we are ducktyping
        nn.Module.__init__(self)

        if isinstance(label_channels_dict, int):
            label_channels_dict = {MultiSpade.DEFAULT_KEY: label_channels_dict}
        self.sort_fn = sort_fn
        self.label_channels: Dict[str, int] = label_channels_dict
        self.spade_layers: nn.ModuleDict = nn.ModuleDict(
            {
                key: SPADE(config_text, norm_nc, label_nc)
                for key, label_nc in label_channels_dict.items()
            }
        )

    def forward(self, x: Tensor, segmaps_dict: Dict[str, Tensor]):
        """
        Args:
            x: input
            segmaps_dict: list of segmaps

        Returns: transformed x
        """
        if isinstance(segmaps_dict, Tensor):
            segmaps_dict = self.try_fix_segmaps_dict(segmaps_dict)
        assert len(segmaps_dict) == len(
            self.spade_layers
        ), f"{len(segmaps_dict)=} != {len(self.spade_layers)=}"
        # run sequentially in the requested order
        for key, segmap in self.sort_fn(segmaps_dict.items()):
            layer = self.spade_layers[key]
            x = layer(x, segmap)
        return x

    def try_fix_segmaps_dict(self, tensor: Tensor):
        if len(self.spade_layers) == 1:
            key = list(self.spade_layers.keys())[0]
            segmaps_dict = {key: tensor}
            return segmaps_dict
        else:
            raise ValueError(
                "You passed a single Tensor, but I don't know which spade layer to "
                "pass it through. My spade layers are:\n"
                f"{self.spade_layers}."
            )
