from options.base_options import BaseOptions


class TrainOptions(BaseOptions):
    def initialize(self, parser):
        parser = BaseOptions.initialize(self, parser)
        # data
        parser.add_argument("--no_shuffle", action="store_true", help="don't shuffle input data")
        # checkpoints
        parser.add_argument(
            "--save_count",
            type=int,
            help="how often to save a checkpoint, in steps",
            default=1000,
        )
        # optimization
        parser.add_argument(
            "--lr", type=float, default=0.0001, help="initial learning rate for adam"
        )
        parser.add_argument(
            "--keep_epochs",
            type=int,
            help="number of epochs with initial learning rate",
            default=5,
        )
        parser.add_argument(
            "--decay_epochs",
            type=int,
            help="number of epochs to linearly decay the learning rate",
            default=5,
        )
        self.isTrain = True
        return parser
