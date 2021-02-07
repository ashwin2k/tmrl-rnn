import numpy as np
import cv2
from tmrl.memory_dataloading import MemoryDataloading, TrajMemoryDataloading


# LOCAL BUFFER COMPRESSION ==============================

def get_local_buffer_sample(prev_act, obs, rew, done, info):
    """
    Input:
        prev_act: action computed from a previous observation and applied to yield obs in the transition
        obs, rew, done, info: outcome of the transition
    this function creates the object that will actually be stored in local buffers for networking
    this is to compress the sample before sending it over the Internet/local network
    buffers of such samples will be given as input to the append() method of the dataloading memory
    the user must define both this function and the append() method of the dataloading memory
    CAUTION: prev_act is the action that comes BEFORE obs (i.e. prev_obs, prev_act(prev_obs), obs(prev_act))
    """
    obs_mod = (obs[0], obs[1][-1])  # speed and most recent image only
    rew_mod = np.float32(rew)
    done_mod = done
    return prev_act, obs_mod, rew_mod, done_mod, info


def get_local_buffer_sample_tm20_imgs(prev_act, obs, rew, done, info):
    """
    Sample compressor for MemoryTM2020
    Input:
        prev_act: action computed from a previous observation and applied to yield obs in the transition
        obs, rew, done, info: outcome of the transition
    this function creates the object that will actually be stored in local buffers for networking
    this is to compress the sample before sending it over the Internet/local network
    buffers of such samples will be given as input to the append() method of the dataloading memory
    the user must define both this function and the append() method of the dataloading memory
    CAUTION: prev_act is the action that comes BEFORE obs (i.e. prev_obs, prev_act(prev_obs), obs(prev_act))
    """
    prev_act_mod = prev_act
    compressed_img = cv2.imencode('.PNG', np.moveaxis(obs[3][-1], 0, -1))
    obs_mod = (obs[0], obs[1], obs[2], compressed_img)  # speed, gear, rpm, last image
    rew_mod = np.float32(rew)
    done_mod = done
    info_mod = info
    return prev_act_mod, obs_mod, rew_mod, done_mod, info_mod


def get_local_buffer_sample_cognifly(prev_act, obs, rew, done, info):
    """
    Sample compressor for cognifly
    """
    prev_act_mod = prev_act
    obs_mod = (obs[0], obs[1], obs[2], obs[3], obs[4], obs[5])  # alt, vel, acc, tar, total_delay, total_delay_kappa
    rew_mod = rew
    done_mod = done
    info_mod = info
    return prev_act_mod, obs_mod, rew_mod, done_mod, info_mod


# MEMORY DATALOADING ===========================================

class MemoryTMNF(MemoryDataloading):
    def __init__(self,
                 memory_size,
                 batchsize,
                 path_loc="",
                 imgs_obs=4,
                 act_buf_len=1,
                 nb_steps=1,
                 use_dataloader=False,
                 num_workers=0,
                 pin_memory=False,
                 remove_size=100,
                 obs_preprocessor: callable = None,
                 sample_preprocessor: callable = None,
                 crc_debug=False,
                 device="cpu"):
        self.imgs_obs = imgs_obs
        self.act_buf_len = act_buf_len
        self.min_samples = max(self.imgs_obs, self.act_buf_len)
        self.start_imgs_offset = max(0, self.min_samples - self.imgs_obs)
        self.start_acts_offset = max(0, self.min_samples - self.act_buf_len)
        super().__init__(memory_size=memory_size,
                         batchsize=batchsize,
                         path_loc=path_loc,
                         nb_steps=nb_steps,
                         use_dataloader=use_dataloader,
                         num_workers=num_workers,
                         pin_memory=pin_memory,
                         remove_size=remove_size,
                         obs_preprocessor=obs_preprocessor,
                         sample_preprocessor=sample_preprocessor,
                         crc_debug=crc_debug,
                         device=device)

    def append_buffer(self, buffer):  # TODO
        return self

    def __len__(self):
        if len(self.data) == 0:
            return 0
        res = len(self.data[0]) - self.min_samples - 1
        if res < 0:
            return 0
        else:
            return res

    def get_transition(self, item):  # TODO
        pass
        # return last_obs, new_act, rew, new_obs, done


class MemoryTMNFLidar(MemoryTMNF):
    def get_transition(self, item):
        """
        CAUTION: item is the first index of the 4 images in the images history of the OLD observation
        CAUTION: in the buffer, a sample is (act, obs(act)) and NOT (obs, act(obs))
            i.e. in a sample, the observation is what step returned after being fed act
            therefore, in the RTRL setting, act is appended to obs
        So we load 5 images from here...
        Don't forget the info dict for CRC debugging
        """
        idx_last = item + self.min_samples - 1
        idx_now = item + self.min_samples
        acts = self.load_acts(item)
        last_act_buf = acts[:-1]
        new_act_buf = acts[1:]
        imgs = self.load_imgs(item)
        last_obs = (self.data[2][idx_last], imgs[:-1], *last_act_buf)
        rew = np.float32(self.data[5][idx_now])
        new_act = self.data[1][idx_now]
        new_obs = (self.data[2][idx_now], imgs[1:], *new_act_buf)
        done = self.data[4][idx_now]
        info = self.data[6][idx_now]
        return last_obs, new_act, rew, new_obs, done, info

    def load_imgs(self, item):
        res = self.data[3][(item + self.start_imgs_offset):(item + self.start_imgs_offset + self.imgs_obs + 1)]
        # res = []
        # for i in range(item, item+self.imgs_obs+1):
        #     img = self.data[3][i]
        #     res.append(img)
        return np.stack(res)

    def load_acts(self, item):
        res = self.data[1][(item + self.start_acts_offset):(item + self.start_acts_offset + self.act_buf_len + 1)]
        return res

    def append_buffer(self, buffer):
        """
        buffer is a list of samples ( act, obs, rew, done, info)
        don't forget to keep the info dictionary in the sample for CRC debugging
        """

        first_data_idx = self.data[0][-1] + 1 if self.__len__() > 0 else 0

        d0 = [first_data_idx + i for i, _ in enumerate(buffer.memory)]  # indexes
        d1 = [b[0] for b in buffer.memory]  # actions
        d2 = [b[1][0] for b in buffer.memory]  # speeds
        d3 = [b[1][1] for b in buffer.memory]  # lidar
        d4 = [b[3] for b in buffer.memory]  # dones
        d5 = [b[2] for b in buffer.memory]  # rewards
        d6 = [b[4] for b in buffer.memory]  # infos

        if self.__len__() > 0:
            self.data[0] += d0
            self.data[1] += d1
            self.data[2] += d2
            self.data[3] += d3
            self.data[4] += d4
            self.data[5] += d5
            self.data[6] += d6
        else:
            self.data.append(d0)
            self.data.append(d1)
            self.data.append(d2)
            self.data.append(d3)
            self.data.append(d4)
            self.data.append(d5)
            self.data.append(d6)

        to_trim = self.__len__() - self.memory_size
        if to_trim > 0:
            self.data[0] = self.data[0][to_trim:]
            self.data[1] = self.data[1][to_trim:]
            self.data[2] = self.data[2][to_trim:]
            self.data[3] = self.data[3][to_trim:]
            self.data[4] = self.data[4][to_trim:]
            self.data[5] = self.data[5][to_trim:]
            self.data[6] = self.data[6][to_trim:]

        return self


class TrajMemoryTMNF(TrajMemoryDataloading):
    def __init__(self,
                 memory_size,
                 batchsize,
                 path_loc="",
                 imgs_obs=4,
                 act_buf_len=1,
                 traj_len=1,
                 nb_steps=1,
                 use_dataloader=False,
                 num_workers=0,
                 pin_memory=False,
                 remove_size=100,
                 obs_preprocessor: callable = None,
                 crc_debug=False,
                 device="cpu"):
        self.imgs_obs = imgs_obs
        self.act_buf_len = act_buf_len
        self.traj_len = traj_len
        self.min_samples = max(self.imgs_obs, self.act_buf_len)
        self.min_samples += self.traj_len - 1
        self.start_imgs_offset = max(0, self.min_samples - self.imgs_obs)
        self.start_acts_offset = max(0, self.min_samples - self.act_buf_len)
        super().__init__(memory_size=memory_size,
                         batchsize=batchsize,
                         path_loc=path_loc,
                         nb_steps=nb_steps,
                         use_dataloader=use_dataloader,
                         num_workers=num_workers,
                         pin_memory=pin_memory,
                         remove_size=remove_size,
                         obs_preprocessor=obs_preprocessor,
                         crc_debug=crc_debug,
                         device=device)

    def append_buffer(self, buffer):  # TODO
        return self

    def __len__(self):
        if len(self.data) == 0:
            return 0
        res = len(self.data[0]) - self.min_samples - 1
        if res < 0:
            return 0
        else:
            return res

    def get_trajectory(self, item):  # TODO
        pass
        # return last_obs, new_act, rew, new_obs, done


class TrajMemoryTMNFLidar(TrajMemoryTMNF):
    def get_trajectory(self, item):
        """
        CAUTION: item is the first index of the 4 images in the images history of the OLD observation
        CAUTION: in the buffer, a sample is (act, obs(act)) and NOT (obs, act(obs))
            i.e. in a sample, the observation is what step returned after being fed act
            therefore, in the RTRL setting, act is appended to obs
        So we load 5 images from here...
        Don't forget the info dict for CRC debugging
        """
        idx_now = item + self.min_samples
        all_acts = self.load_acts_traj(item)
        # new_act_buf = acts[1:]
        all_imgs = self.load_imgs_traj(item)

        # rew = np.float32(self.data[5][idx_now])

        rew_traj = [np.float32(self.data[5][idx_now + i]) for i in range(self.traj_len)]

        # new_act = self.data[1][idx_now]

        # new_obs = (self.data[2][idx_now], imgs[1:], *new_act_buf)

        augm_obs_traj = [(self.data[2][idx_now + i], all_imgs[1 + i:self.imgs_obs + i + 1], *all_acts[1 + i:self.act_buf_len + i + 1]) for i in range(self.traj_len)]

        # done = self.data[4][idx_now]

        done_traj = [self.data[4][idx_now + i] for i in range(self.traj_len)]

        # info = self.data[6][idx_now]

        info_traj = [self.data[6][idx_now + i] for i in range(self.traj_len)]

        return augm_obs_traj, rew_traj, done_traj, info_traj

    def load_imgs_traj(self, item):
        res = self.data[3][(item + self.start_imgs_offset):(item + self.start_imgs_offset + self.imgs_obs + self.traj_len)]
        return np.stack(res)

    def load_acts_traj(self, item):
        res = self.data[1][(item + self.start_acts_offset):(item + self.start_acts_offset + self.act_buf_len + self.traj_len)]
        return res

    def append_buffer(self, buffer):
        """
        buffer is a list of samples ( act, obs, rew, done, info)
        don't forget to keep the info dictionary in the sample for CRC debugging
        """

        first_data_idx = self.data[0][-1] + 1 if self.__len__() > 0 else 0

        d0 = [first_data_idx + i for i, _ in enumerate(buffer.memory)]  # indexes
        d1 = [b[0] for b in buffer.memory]  # actions
        d2 = [b[1][0] for b in buffer.memory]  # speeds
        d3 = [b[1][1] for b in buffer.memory]  # lidar
        d4 = [b[3] for b in buffer.memory]  # dones
        d5 = [b[2] for b in buffer.memory]  # rewards
        d6 = [b[4] for b in buffer.memory]  # infos

        if self.__len__() > 0:
            self.data[0] += d0
            self.data[1] += d1
            self.data[2] += d2
            self.data[3] += d3
            self.data[4] += d4
            self.data[5] += d5
            self.data[6] += d6
        else:
            self.data.append(d0)
            self.data.append(d1)
            self.data.append(d2)
            self.data.append(d3)
            self.data.append(d4)
            self.data.append(d5)
            self.data.append(d6)

        to_trim = self.__len__() - self.memory_size
        if to_trim > 0:
            self.data[0] = self.data[0][to_trim:]
            self.data[1] = self.data[1][to_trim:]
            self.data[2] = self.data[2][to_trim:]
            self.data[3] = self.data[3][to_trim:]
            self.data[4] = self.data[4][to_trim:]
            self.data[5] = self.data[5][to_trim:]
            self.data[6] = self.data[6][to_trim:]

        return self


class MemoryTM2020(MemoryDataloading):
    def __init__(self,
                 memory_size,
                 batchsize,
                 path_loc="",
                 imgs_obs=4,
                 act_buf_len=1,
                 nb_steps=1,
                 use_dataloader=False,
                 num_workers=0,
                 pin_memory=False,
                 remove_size=100,
                 obs_preprocessor: callable = None,
                 sample_preprocessor: callable = None,
                 crc_debug=False,
                 device="cpu"):
        self.imgs_obs = imgs_obs
        self.act_buf_len = act_buf_len
        self.min_samples = max(self.imgs_obs, self.act_buf_len)
        self.start_imgs_offset = max(0, self.min_samples - self.imgs_obs)
        self.start_acts_offset = max(0, self.min_samples - self.act_buf_len)
        super().__init__(memory_size=memory_size,
                         batchsize=batchsize,
                         path_loc=path_loc,
                         nb_steps=nb_steps,
                         use_dataloader=use_dataloader,
                         num_workers=num_workers,
                         pin_memory=pin_memory,
                         remove_size=remove_size,
                         obs_preprocessor=obs_preprocessor,
                         sample_preprocessor=sample_preprocessor,
                         crc_debug=crc_debug,
                         device=device)

    def append_buffer(self, buffer):
        """
        buffer is a list of samples (act, obs, rew, done, info)
        don't forget to keep the info dictionary in the sample for CRC debugging
        """
        first_data_idx = self.data[0][-1] + 1 if self.__len__() > 0 else 0
        d0 = [(first_data_idx + i) % self.memory_size for i, _ in enumerate(buffer.memory)]  # indexes  # FIXME: check that this works
        d1 = [b[0] for b in buffer.memory]  # actions
        d2 = [b[1][0] for b in buffer.memory]  # speeds
        d3 = [b[1][1] for b in buffer.memory]  # gear
        d4 = [b[1][2] for b in buffer.memory]  # rpm
        for bi, di in zip(buffer.memory, d0):
            cv2.imwrite(str(self.path / (str(di) + '.png')), cv2.imdecode(np.array(bi[1][3][1]), cv2.IMREAD_UNCHANGED))
        d5 = [b[3] for b in buffer.memory]  # dones
        d6 = [b[2] for b in buffer.memory]  # rewards
        d7 = [b[4] for b in buffer.memory]  # infos

        if self.__len__() > 0:
            self.data[0] += d0
            self.data[1] += d1
            self.data[2] += d2
            self.data[3] += d3
            self.data[4] += d4
            self.data[5] += d5
            self.data[6] += d6
            self.data[7] += d7
        else:
            self.data.append(d0)
            self.data.append(d1)
            self.data.append(d2)
            self.data.append(d3)
            self.data.append(d4)
            self.data.append(d5)
            self.data.append(d6)
            self.data.append(d7)

        to_trim = self.__len__() - self.memory_size
        if to_trim > 0:
            self.data[0] = self.data[0][to_trim:]
            self.data[1] = self.data[1][to_trim:]
            self.data[2] = self.data[2][to_trim:]
            self.data[3] = self.data[3][to_trim:]
            self.data[4] = self.data[4][to_trim:]
            self.data[5] = self.data[5][to_trim:]
            self.data[6] = self.data[6][to_trim:]
            self.data[7] = self.data[7][to_trim:]
        return self

    def __len__(self):
        if len(self.data) < self.min_samples + 1:
            return 0
        res = len(self.data[0]) - self.min_samples - 1
        if res < 0:
            return 0
        else:
            return res

    def get_transition(self, item):
        idx_last = item + self.min_samples - 1
        idx_now = item + self.min_samples

        imgs = self.load_imgs(item)
        acts = self.load_acts(item)

        last_act_buf = acts[:-1]
        new_act_buf = acts[1:]

        last_obs = (self.data[2][idx_last], self.data[3][idx_last], self.data[4][idx_last], imgs[:-1], *last_act_buf)
        rew = np.float32(self.data[6][idx_now])
        new_act = np.array(self.data[1][idx_now], dtype=np.float32)
        new_obs = (self.data[2][idx_now], self.data[3][idx_now], self.data[4][idx_now], imgs[1:], *new_act_buf)
        done = self.data[5][idx_now]
        info = self.data[7][idx_now]
        return last_obs, new_act, rew, new_obs, done, info

    def load_imgs(self, item):
        res = []
        for i in range(item + self.start_imgs_offset, item + self.start_imgs_offset + self.imgs_obs + 1):
            img = cv2.imread(str(self.path / (str(self.data[0][i]) + ".png")))
            res.append(np.moveaxis(img, -1, 0))
        return np.array(res)

    def load_acts(self, item):
        res = self.data[1][(item + self.start_acts_offset):(item + self.start_acts_offset + self.act_buf_len + 1)]
        return res


class MemoryCognifly(MemoryDataloading):
    def __init__(self,
                 memory_size,
                 batchsize,
                 path_loc="",
                 imgs_obs=4,
                 act_buf_len=1,
                 nb_steps=1,
                 use_dataloader=False,
                 num_workers=0,
                 pin_memory=False,
                 remove_size=100,
                 obs_preprocessor: callable = None,
                 sample_preprocessor: callable = None,
                 crc_debug=False,
                 device="cpu"):
        self.imgs_obs = imgs_obs
        self.act_buf_len = act_buf_len
        self.min_samples = max(self.imgs_obs, self.act_buf_len)
        self.start_imgs_offset = max(0, self.min_samples - self.imgs_obs)
        self.start_acts_offset = max(0, self.min_samples - self.act_buf_len)
        print(f"DEBUG: self.imgs_obs:{self.imgs_obs}, self.act_buf_len:{self.act_buf_len}, self.min_sample:{self.min_samples}, self.start_imgs_offset:{self.start_imgs_offset}, self.start_acts_offset:{self.start_acts_offset}")
        super().__init__(memory_size=memory_size,
                         batchsize=batchsize,
                         path_loc=path_loc,
                         nb_steps=nb_steps,
                         use_dataloader=use_dataloader,
                         num_workers=num_workers,
                         pin_memory=pin_memory,
                         remove_size=remove_size,
                         obs_preprocessor=obs_preprocessor,
                         sample_preprocessor=sample_preprocessor,
                         crc_debug=crc_debug,
                         device=device)

    def append_buffer(self, buffer):
        first_data_idx = self.data[0][-1] + 1 if self.__len__() > 0 else 0
        d0 = [(first_data_idx + i) % self.memory_size for i, _ in enumerate(buffer.memory)]  # indexes  # FIXME: check that this works
        d1 = [b[0] for b in buffer.memory]  # actions
        d2 = [b[1][0] for b in buffer.memory]  # alt
        d3 = [b[1][1] for b in buffer.memory]  # vel
        d4 = [b[1][2] for b in buffer.memory]  # acc
        d5 = [b[1][3] for b in buffer.memory]  # tar
        d6 = [b[1][4] for b in buffer.memory]  # del
        d7 = [b[1][5] for b in buffer.memory]  # del_k

        d8 = [b[3] for b in buffer.memory]  # dones
        d9 = [b[2] for b in buffer.memory]  # rewards
        d10 = [b[4] for b in buffer.memory]  # infos

        if self.__len__() > 0:
            self.data[0] += d0
            self.data[1] += d1
            self.data[2] += d2
            self.data[3] += d3
            self.data[4] += d4
            self.data[5] += d5
            self.data[6] += d6
            self.data[7] += d7
            self.data[8] += d8
            self.data[9] += d9
            self.data[10] += d10
        else:
            self.data.append(d0)
            self.data.append(d1)
            self.data.append(d2)
            self.data.append(d3)
            self.data.append(d4)
            self.data.append(d5)
            self.data.append(d6)
            self.data.append(d7)
            self.data.append(d8)
            self.data.append(d9)
            self.data.append(d10)

        to_trim = self.__len__() - self.memory_size
        if to_trim > 0:
            self.data[0] = self.data[0][to_trim:]
            self.data[1] = self.data[1][to_trim:]
            self.data[2] = self.data[2][to_trim:]
            self.data[3] = self.data[3][to_trim:]
            self.data[4] = self.data[4][to_trim:]
            self.data[5] = self.data[5][to_trim:]
            self.data[6] = self.data[6][to_trim:]
            self.data[7] = self.data[7][to_trim:]
            self.data[8] = self.data[8][to_trim:]
            self.data[9] = self.data[9][to_trim:]
            self.data[10] = self.data[10][to_trim:]
        return self

    def __len__(self):
        if len(self.data) < self.min_samples + 1:
            return 0
        res = len(self.data[0]) - self.min_samples - 1
        if res < 0:
            return 0
        else:
            return res

    def get_transition(self, item):
        idx_last = item + self.min_samples - 1
        idx_now = item + self.min_samples
        acts = self.load_acts(item)
        last_act_buf = acts[:-1]
        new_act_buf = acts[1:]
        last_obs = (self.data[2][idx_last], self.data[3][idx_last], self.data[4][idx_last], self.data[5][idx_last], self.data[6][idx_last], self.data[7][idx_last], *last_act_buf)
        rew = np.float32(self.data[9][idx_now])
        new_act = np.array(self.data[1][idx_now], dtype=np.float32)
        new_obs = (self.data[2][idx_now], self.data[3][idx_now], self.data[4][idx_now], self.data[5][idx_now], self.data[6][idx_now], self.data[7][idx_now], *new_act_buf)
        done = self.data[8][idx_now]
        info = self.data[10][idx_now]
        return last_obs, new_act, rew, new_obs, done, info

    def load_imgs(self, item):  # TODO
        res = []
        for i in range(item, item+self.imgs_obs+1):
            img = cv2.imread(str(self.path / (str(self.data[0][i]) + ".png")))
            res.append(np.moveaxis(img, -1, 0))
        return np.array(res)

    def load_acts(self, item):
        res = self.data[1][(item + self.start_acts_offset):(item + self.start_acts_offset + self.act_buf_len + 1)]
        return res