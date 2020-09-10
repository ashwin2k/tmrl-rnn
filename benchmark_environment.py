import gym
from gym import spaces
import random
import time

NB_STEPS = 1000
ACT_COMPUTE_MIN = 0.05
ACT_COMPUTE_MAX = 0.04

action_space = spaces.Box(low=0.0, high=1.0, shape=(4,))
env = gym.make("gym_tmrl:gym-tmrl-v0")

t_d = time.time()
obs = env.reset()
for idx in range(NB_STEPS-1):
    act = action_space.sample()
    time.sleep(random.uniform(ACT_COMPUTE_MIN, ACT_COMPUTE_MAX))
    o, r, d, i = env.step(act)
t_f = time.time()

elapsed_time = t_f - t_d
print(f"benchmark results: obs capture: {env.benchmarks()}")
print(f"elapsed time: {elapsed_time}")
print(f"time-step duration: {elapsed_time / NB_STEPS}")

