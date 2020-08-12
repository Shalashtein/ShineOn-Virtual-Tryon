import logging
import os.path as osp
import signal
import sys
import traceback

from pytorch_lightning import Trainer

import log
from models import find_model_using_name
from options.test_options import TestOptions
from options.train_options import TrainOptions

logger = log.setup_custom_logger("logger")



def main(train=True):
    options_obj = TrainOptions() if train else TestOptions()
    opt = options_obj.parse()
    logger.setLevel(getattr(logging, opt.loglevel.upper()))

    model_class = find_model_using_name(opt.model)
    if opt.checkpoint or not train:
        model = model_class.load_from_checkpoint(opt.checkpoint)
    else:
        model = model_class(opt)

    root_dir = osp.join(opt.experiments_dir, opt.name)
    trainer = Trainer(
        gpus=opt.gpu_ids,
        default_root_dir=root_dir,
        log_save_interval=opt.display_count,
        fast_dev_run=opt.fast_dev_run,
        max_epochs=opt.keep_epochs + opt.decay_epochs,
    )

    def save_on_interrupt(*args):
        ckpt_path = osp.join(trainer.checkpoint_callback.dirpath, "latest.ckpt")
        logger.error(f"Interrupt detected, saving Trainer checkpoint to {ckpt_path}!")
        trainer.save_checkpoint(ckpt_path)
        exit()

    signal.signal(signal.SIGINT, save_on_interrupt)

    if train:
        try:
            trainer.fit(model)
        except Exception as e:
            logger.error(traceback.format_exc())
            save_on_interrupt()
    else:
        trainer.test(model)

    logger.info(f"Finished {opt.model}, named {opt.name}!")


if __name__ == "__main__":
    main(train=True)
