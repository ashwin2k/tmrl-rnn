import atexit
import gc
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from os.path import exists
from random import randrange
from tempfile import mkdtemp
import pandas as pd
import yaml
from abc import ABC, abstractmethod

from tmrl.networking import TrainerInterface
from tmrl.util import dump, git_info, load, load_json, partial, partial_from_dict, partial_to_dict, save_json
import tmrl.config.config_constants as cfg
import tmrl.config.config_objects as cfg_obj


def log_environment_variables():
    """
    add certain relevant environment variables to our config
    usage: `LOG_VARIABLES='HOME JOBID' python ...`
    """
    return {k: os.environ.get(k, '') for k in os.environ.get('LOG_VARIABLES', '').strip().split()}


def load_run_instance(checkpoint_path):
    """
    Default function used to load trainers from checkpoint path
    Args:
        checkpoint_path: the path where instances of run_cls are checkpointed
    Returns:
        An instance of run_cls loaded from checkpoint_path
    """
    return load(checkpoint_path)


def dump_run_instance(run_instance, checkpoint_path):
    """
    Default function used to dump trainers to checkpoint path
    Args:
        run_instance: the instance of run_cls to checkpoint
        checkpoint_path: the path where instances of run_cls are checkpointed
    """
    dump(run_instance, checkpoint_path)


def iterate_epochs_tm(run_cls,
                      interface: TrainerInterface,
                      checkpoint_path: str,
                      dump_run_instance_fn=dump_run_instance,
                      load_run_instance_fn=load_run_instance,
                      epochs_between_checkpoints=1):
    """
    Main training loop (remote)
    The run_cls instance is saved in checkpoint_path at the end of each epoch
    The model weights are sent to the RolloutWorker every model_checkpoint_interval epochs
    Generator yielding episode statistics (list of pd.Series) while running and checkpointing
    """
    checkpoint_path = checkpoint_path or tempfile.mktemp("_remove_on_exit")

    try:
        if not exists(checkpoint_path):
            logging.info(f"=== specification ".ljust(70, "="))
            run_instance = run_cls()
            dump_run_instance_fn(run_instance, checkpoint_path)
            logging.info(f"")
        else:
            logging.info(f" Loading checkpoint...")
            t1 = time.time()
            run_instance = load_run_instance_fn(checkpoint_path)
            logging.info(f" Loaded checkpoint in {time.time() - t1} seconds.")

        while run_instance.epoch < run_instance.epochs:
            # time.sleep(1)  # on network file systems writing files is asynchronous and we need to wait for sync
            yield run_instance.run_epoch(interface=interface)  # yield stats data frame (this makes this function a generator)
            if run_instance.epoch % epochs_between_checkpoints == 0:
                logging.info(f" saving checkpoint...")
                t1 = time.time()
                dump_run_instance_fn(run_instance, checkpoint_path)
                logging.info(f" saved checkpoint in {time.time() - t1} seconds.")
                # we delete and reload the run_instance from disk to ensure the exact same code runs regardless of interruptions
                # del run_instance
                # gc.collect()  # garbage collection
                # run_instance = load_run_instance_fn(checkpoint_path)

    finally:
        if checkpoint_path.endswith("_remove_on_exit") and exists(checkpoint_path):
            os.remove(checkpoint_path)


def run_with_wandb(entity, project, run_id, interface, run_cls, checkpoint_path: str = None, dump_run_instance_fn=None, load_run_instance_fn=None):
    """
    main training loop (remote)
    saves config and stats to https://wandb.com
    """
    dump_run_instance_fn = dump_run_instance_fn or dump_run_instance
    load_run_instance_fn = load_run_instance_fn or load_run_instance
    wandb_dir = mkdtemp()  # prevent wandb from polluting the home directory
    atexit.register(shutil.rmtree, wandb_dir, ignore_errors=True)  # clean up after wandb atexit handler finishes
    import wandb
    logging.debug(f" run_cls: {run_cls}")
    config = partial_to_dict(run_cls)
    config['seed'] = config['seed'] or randrange(1, 1000000)  # if seed == 0 replace with random
    config['environ'] = log_environment_variables()
    # config['git'] = git_info()  # TODO: check this for bugs
    resume = checkpoint_path and exists(checkpoint_path)
    wandb.init(dir=wandb_dir, entity=entity, project=project, id=run_id, resume=resume, config=config)
    # logging.info(config)
    for stats in iterate_epochs_tm(run_cls, interface, checkpoint_path, dump_run_instance_fn, load_run_instance_fn):
        [wandb.log(json.loads(s.to_json())) for s in stats]


def run(interface, run_cls, checkpoint_path: str = None, dump_run_instance_fn=None, load_run_instance_fn=None):
    """
    main training loop (remote)
    """
    dump_run_instance_fn = dump_run_instance_fn or dump_run_instance
    load_run_instance_fn = load_run_instance_fn or load_run_instance
    for stats in iterate_epochs_tm(run_cls, interface, checkpoint_path, dump_run_instance_fn, load_run_instance_fn):
        pass


class Trainer:
    def __init__(self,
                 training_cls=cfg_obj.TRAINER,
                 server_ip=cfg.SERVER_IP_FOR_TRAINER,
                 model_path=cfg.MODEL_PATH_TRAINER):
        self.training_cls = training_cls
        self.interface = TrainerInterface(server_ip=server_ip,
                                          model_path=model_path)

    def run(self,
            checkpoint_path=cfg.CHECKPOINT_PATH,
            dump_run_instance_fn: callable = None,
            load_run_instance_fn: callable = None):
        run(interface=self.interface,
            run_cls=self.training_cls,
            checkpoint_path=checkpoint_path,
            dump_run_instance_fn=dump_run_instance_fn,
            load_run_instance_fn=load_run_instance_fn)

    def run_with_wandb(self,
                       entity=cfg.WANDB_ENTITY,
                       project=cfg.WANDB_PROJECT,
                       run_id=cfg.WANDB_RUN_ID,
                       checkpoint_path=cfg.CHECKPOINT_PATH,
                       dump_run_instance_fn: callable = None,
                       load_run_instance_fn: callable = None):
        run_with_wandb(entity=entity,
                       project=project,
                       run_id=run_id,
                       interface=self.interface,
                       run_cls=self.training_cls,
                       checkpoint_path=checkpoint_path,
                       dump_run_instance_fn=dump_run_instance_fn,
                       load_run_instance_fn=load_run_instance_fn)


class TrainingAgent(ABC):
    def __init__(self,
                 observation_space,
                 action_space,
                 device):
        """
        observation_space, action_space and device are here for your convenience.

        You are free to use them or not, but your subclass must have them as args or kwargs of __init__() .
        """
        self.observation_space = observation_space
        self.action_space = action_space
        self.device = device

    @abstractmethod
    def train(self, batch):
        """
        Executes a training step.

        Args:
            batch: tuple or batched torch.tensors (previous observation, action, reward, new observation, done)

        Returns:
            ret_dict: dictionary: a dictionary containing one entry per metric you wish to log (e.g. for wandb)
        """
        raise NotImplementedError

    @abstractmethod
    def get_actor(self):
        """
        Returns the current ActorModule to be broadcast to the RolloutWorkers.

        Returns:
             actor: ActorModule: current actor to be broadcast
        """
        raise NotImplementedError


