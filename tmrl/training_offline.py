from dataclasses import dataclass
import pandas as pd
from pandas import DataFrame, Timestamp
# import gym
import time

import tmrl.sac
from tmrl.util import pandas_dict, cached_property
from tmrl.networking import TrainerInterface

from tmrl.envs import Env

from tmrl.memory_dataloading import MemoryDataloading

import torch

# import pybullet_envs


@dataclass(eq=0)
class TrainingOffline:
    Env: type = Env
    Agent: type = tmrl.sac.Agent
    Memory: type = MemoryDataloading
    use_dataloader: bool = False  # Whether to use pytorch dataloader for multiprocess dataloading
    nb_workers: int = 0  # Number of parallel workers in pytorch dataloader
    batchsize: int = 256  # training batch size
    memory_size: int = 1000000  # replay memory size
    epochs: int = 10  # total number of epochs, we save the agent every epoch
    rounds: int = 50  # number of rounds per epoch, we generate statistics every round
    steps: int = 2000  # number of steps per round
    update_model_interval: int = 100  # number of steps between model broadcasts
    # update_buffer_interval: int = 100  # number of steps between retrieving buffered experiences in the interface
    max_training_steps_per_env_step: float = 1.0  # training will pause when above this ratio
    sleep_between_buffer_retrieval_attempts: float = 0.1  # algorithm will sleep for this amount of time when waiting for needed incoming samples
    stats_window: int = None  # default = steps, should be at least as long as a single episode
    seed: int = 0  # seed is currently not used
    tag: str = ''  # for logging, e.g. allows to compare groups of runs
    profiling: bool = False  # if True, run_epoch will be profiled and the profiling will be printed at the enc of each epoch

    device: str = None
    total_updates = 0

    def __post_init__(self):
        device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.epoch = 0
        # print(self.Agent)
        # print(self.Env)
        self.memory = self.Memory(memory_size=self.memory_size,
                                  batchsize=self.batchsize,
                                  nb_steps=self.steps,
                                  use_dataloader=False,
                                  device=device)
        self.agent = self.Agent(Env=self.Env, device=device)
        self.total_samples = len(self.memory)
        print(f"INFO: Initial total_samples:{self.total_samples}")

    def update_buffer(self, interface):
        buffer = interface.retrieve_buffer()
        self.memory.append(buffer)
        self.total_samples += len(buffer)

    def check_ratio(self, interface):
        ratio = self.total_updates / self.total_samples if self.total_samples > 0.0 else -1.0
        if ratio > self.max_training_steps_per_env_step or ratio == -1.0:
            print("INFO: Waiting for new samples")
            while ratio > self.max_training_steps_per_env_step or ratio == -1.0:
                # wait for new samples
                self.update_buffer(interface)
                ratio = self.total_updates / self.total_samples if self.total_samples > 0.0 else -1.0
                if ratio > self.max_training_steps_per_env_step or ratio == -1.0:
                    time.sleep(self.sleep_between_buffer_retrieval_attempts)

    def run_epoch(self, interface: TrainerInterface):
        stats = []
        state = None

        for rnd in range(self.rounds):
            print(f"=== epoch {self.epoch}/{self.epochs} ".ljust(20, '=') + f" round {rnd}/{self.rounds} ".ljust(50, '='))
            print(f"DEBUG: SAC (Training): current memory size:{len(self.memory)}")

            stats_training = []

            t0 = pd.Timestamp.utcnow()
            self.check_ratio(interface)
            t1 = pd.Timestamp.utcnow()

            if self.profiling:
                from pyinstrument import Profiler
                pro = Profiler()
                pro.start()

            # retrieve local buffer in replay memory
            self.update_buffer(interface)

            for batch in self.memory:
                if self.total_updates == 0:
                    print("starting training")
                stats_training_dict = self.agent.train(batch)
                stats_training_dict["return_test"] = self.memory.stat_test_return
                stats_training_dict["return_train"] = self.memory.stat_train_return
                stats_training_dict["episode_length_test"] = self.memory.stat_test_steps
                stats_training_dict["episode_length_train"] = self.memory.stat_train_steps
                stats_training += stats_training_dict,
                self.total_updates += 1
                if self.total_updates % self.update_model_interval == 0:
                    # broadcast model weights
                    interface.broadcast_model(self.agent.model_nograd.actor)
                self.check_ratio(interface)
            
            round_time = Timestamp.utcnow() - t0
            idle_time = t1 - t0
            stats += pandas_dict(
                memory_size=len(self.memory),
                round_time=round_time,
                idle_time=idle_time,
                **DataFrame(stats_training).mean(skipna=True)
            ),

            print(stats[-1].add_prefix("  ").to_string(), '\n')

            if self.profiling:
                pro.stop()
                print(pro.output_text(unicode=True, color=False))

        self.epoch += 1
        return stats