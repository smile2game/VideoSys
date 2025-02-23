import logging
import os
from pprint import pformat

import numpy as np
import pandas as pd
import torch
from PIL import ImageFile
from torchvision.datasets.folder import IMG_EXTENSIONS, pil_loader

from .aspect import COMMON_AR, update_common_ar
from .read_video import read_video
from .utils import (
    VID_EXTENSIONS,
    get_transforms_image,
    get_transforms_video,
    read_file,
    remove_interval,
    split_df_by_rank,
    temporal_random_crop,
)

ImageFile.LOAD_TRUNCATED_IMAGES = True
IMG_FPS = 120


def half_normal_pdf(x, sigma=1.0):
    if x < 0:
        return 0

    distribution = torch.distributions.normal.Normal(0, sigma)
    return np.sqrt(2 / np.pi) * torch.exp(distribution.log_prob(torch.tensor([x], dtype=torch.float))).numpy()


class VideoTextDataset(torch.utils.data.Dataset):
    """load video according to the csv file.

    Args:
        target_video_len (int): the number of video frames will be load.
        align_transform (callable): Align different videos in a specified size.
        temporal_sample (callable): Sample the target length of a video.
    """

    def __init__(
        self,
        data_path=None,
        num_frames=16,
        frame_interval=1,
        image_size=(256, 256),
        transform_name="center",
    ):
        self.data_path = data_path
        self.data = read_file(data_path)
        self.get_text = "text" in self.data.columns
        self.num_frames = num_frames
        self.frame_interval = frame_interval
        self.image_size = image_size
        self.transforms = {
            "image": get_transforms_image(transform_name, image_size),
            "video": get_transforms_video(transform_name, image_size),
        }

    def _print_data_number(self):
        num_videos = 0
        num_images = 0
        for path in self.data["path"]:
            if self.get_type(path) == "video":
                num_videos += 1
            else:
                num_images += 1
        print(f"Dataset contains {num_videos} videos and {num_images} images.")

    def get_type(self, path):
        ext = os.path.splitext(path)[-1].lower()
        if ext.lower() in VID_EXTENSIONS:
            return "video"
        else:
            assert ext.lower() in IMG_EXTENSIONS, f"Unsupported file format: {ext}"
            return "image"

    def getitem(self, index):
        sample = self.data.iloc[index]
        path = sample["path"]
        file_type = self.get_type(path)

        if file_type == "video":
            # loading
            vframes, vinfo = read_video(path, backend="av")
            video_fps = vinfo["video_fps"] if "video_fps" in vinfo else 24

            # Sampling video frames
            video = temporal_random_crop(vframes, self.num_frames, self.frame_interval)

            # transform
            transform = self.transforms["video"]
            video = transform(video)  # T C H W
        else:
            # loading
            image = pil_loader(path)
            video_fps = IMG_FPS

            # transform
            transform = self.transforms["image"]
            image = transform(image)

            # repeat
            video = image.unsqueeze(0).repeat(self.num_frames, 1, 1, 1)

        # TCHW -> CTHW
        video = video.permute(1, 0, 2, 3)

        ret = {"video": video, "fps": video_fps}
        if self.get_text:
            ret["text"] = sample["text"]
        return ret

    def __getitem__(self, index):
        for _ in range(10):
            try:
                return self.getitem(index)
            except Exception as e:
                path = self.data.iloc[index]["path"]
                print(f"data {path}: {e}")
                index = np.random.randint(len(self))
        raise RuntimeError("Too many bad data.")

    def __len__(self):
        return len(self.data)


class VariableVideoTextDataset(VideoTextDataset):
    def __init__(
        self,
        data_path=None,
        num_frames=None,
        frame_interval=1,
        image_size=(None, None),
        transform_name=None,
        preprocessed_data=False,
    ):
        super().__init__(data_path, num_frames, frame_interval, image_size, transform_name=None)

        # repeat data if it is the example data
        if "/assets/example_data/demo" in data_path:
            logging.info(
                f"Repeat example data {data_path} 10 times (size: {len(self.data)} -> {len(self.data) * 10}) for easy training."
            )
            self.data = pd.concat([self.data] * 10, ignore_index=True)

        self.transform_name = transform_name
        self.data["id"] = np.arange(len(self.data))
        self.preprocessed_data = preprocessed_data

    def get_data_info(self, index):
        T = self.data.iloc[index]["num_frames"]
        H = self.data.iloc[index]["height"]
        W = self.data.iloc[index]["width"]
        return T, H, W

    def getitem(self, index):
        # a hack to pass in the (time, height, width) info from sampler
        index, num_frames, height, width, ar_name, sp_size, gas = index

        sample = self.data.iloc[index]
        path = sample["path"]
        text = sample["text"]
        ar = height / width
        video_fps = 24  # default fps

        if not self.preprocessed_data:
            file_type = self.get_type(path)
            if file_type == "video":
                # loading
                vframes, vinfo = read_video(path, backend="av")
                video_fps = vinfo["video_fps"] if "video_fps" in vinfo else 24

                # Sampling video frames
                video = temporal_random_crop(vframes, num_frames, self.frame_interval)
                video = video.clone()
                del vframes

                video_fps = video_fps // self.frame_interval

                # transform
                transform = get_transforms_video(self.transform_name, (height, width))
                video = transform(video)  # T C H W
            else:
                # loading
                image = pil_loader(path)
                video_fps = IMG_FPS

                # transform
                transform = get_transforms_image(self.transform_name, (height, width))
                image = transform(image)

                # repeat
                video = image.unsqueeze(0)
        else:
            video = torch.load(sample["vae_emb"], weights_only=False)  # C T H W
            nf = max(num_frames * 5 // 17, 1)
            video = video.permute(1, 0, 2, 3)  # T C H W
            video = temporal_random_crop(video, nf, 1)
            model_args = torch.load(sample["text_emb"], weights_only=False)
            text = model_args["y"]
            mask = model_args["mask"]

        # TCHW -> CTHW
        video = video.permute(1, 0, 2, 3)
        ret = {
            "video": video,
            "num_frames": num_frames,
            "height": height,
            "width": width,
            "ar": ar,
            "fps": video_fps,
            "ar_name": ar_name,
            "sp_size": sp_size,
            "gas": gas,
            "text": text,
        }
        if self.preprocessed_data:
            ret["mask"] = mask
        return ret

    def __getitem__(self, index):
        return self.getitem(index)


class DummyVariableVideoTextDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        data_size,
        seed,
        data_path=None,
        frame_interval=1,
        transform_name=None,
        preprocessed_data=False,
        num_frames=None,
        image_size=(None, None),
        bucket_config=None,
        common_ar=None,
        distribution: str = "zipf",  # uniform or zipf
        zipf_offset: float = 10,
        image_mixing_type: str = "exclusive",
        image_mixing_frac: float = -1.0,
        res_scale: float = None,
        frame_scale: float = None,
    ):
        self.data_size = data_size
        self.seed = seed  # use this generator to ensure global consistency
        self.generator = np.random.default_rng(seed + data_size)  # data are generated on host mem
        self.torch_generator = torch.Generator()
        self.data_path = data_path
        self.preprocessed_data = preprocessed_data
        self.image_mixing_type = image_mixing_type
        self.image_mixing_frac = image_mixing_frac
        self.res_scale = res_scale
        self.frame_scale = frame_scale
        update_common_ar(bucket_config, common_ar)
        logging.info(f"common ar for data synthesis: {pformat(COMMON_AR)}")
        self._build_dummy_dataset(bucket_config, distribution, zipf_offset)

        self.frame_interval = frame_interval
        self.transform_name = transform_name
        self.num_frames = num_frames
        self.image_size = image_size

    def _build_dummy_dataset(self, bucket_config, distribution, zipf_offset):
        self.get_text = False
        assert bucket_config is not None

        data, frame_data = [], []
        log_str = "build dummy dataset:"
        if self.res_scale is not None and self.frame_scale is not None:
            res_list, frame_list = set(), set()
            data_dict = {}
            for ar_name in bucket_config:
                res_list.add(COMMON_AR[ar_name][0])
                for num_frame in bucket_config[ar_name]:
                    if bucket_config[ar_name][num_frame][1] is not None:
                        frame_list.add(num_frame)
                        data_dict[(ar_name, num_frame)] = {}
            res_list = np.array(sorted(list(res_list)))
            frame_list = np.array(sorted(list(frame_list)))

            res_weights = res_list / max(res_list) * self.res_scale
            frame_weights = np.sqrt(np.sqrt(frame_list / max(frame_list))) * self.frame_scale

            total = 0.0
            for ar_name, num_frame in data_dict:
                res_w = res_weights[np.where(res_list == COMMON_AR[ar_name][0])[0][0]]
                frame_w = frame_weights[np.where(frame_list == num_frame)[0][0]]
                prob = half_normal_pdf(res_w) * half_normal_pdf(frame_w)
                data_dict[(ar_name, num_frame)] = dict(
                    res_weight=res_w,
                    frame_weight=frame_w,
                    prob=prob,
                )
                total += prob

            img_cnt, vid_cnt = 0, 0
            keys = sorted(data_dict.keys(), key=lambda x: COMMON_AR[x[0]][0] * x[1])
            for ar_name, num_frame in keys:
                prob = data_dict[(ar_name, num_frame)]["prob"] / total
                data_dict[(ar_name, num_frame)]["prob"] = prob

                cnt = int(prob * self.data_size)
                data_dict[(ar_name, num_frame)]["cnt"] = cnt

                log_str += f"\n ({ar_name}, {num_frame}), cnt: {cnt}"
                if num_frame == 1:
                    img_cnt += cnt
                else:
                    vid_cnt += cnt

                height_width_pool = pd.DataFrame(COMMON_AR[ar_name][1].values(), columns=["height", "width"])
                idx = self.generator.integers(low=0, high=len(height_width_pool), size=(cnt,))
                bucket_data = height_width_pool.iloc[idx]
                bucket_data.reset_index(drop=True, inplace=True)
                data.append(bucket_data)
                frame_data.extend([num_frame] * cnt)
            log_str += f"\nimg_cnt: {img_cnt}, vid_cnt: {vid_cnt}, ratio: {img_cnt / vid_cnt}"
        else:
            # collect valid bucket candidate, only consider ar_name and num_frame
            img_candidates, vid_candidates = [], []
            for ar_name in bucket_config:
                for num_frame in bucket_config[ar_name]:
                    if bucket_config[ar_name][num_frame][1] is not None:
                        if num_frame == 1:
                            img_candidates.append((ar_name, num_frame))
                        else:
                            vid_candidates.append((ar_name, num_frame))
            # sort by total pixels = num_frame * pixels
            vid_candidates = sorted(vid_candidates, key=lambda x: x[1] * COMMON_AR[x[0]][0])
            img_candidates = sorted(img_candidates, key=lambda x: COMMON_AR[x[0]][0])

            if self.image_mixing_type == "inclusive":
                if self.image_mixing_frac < 0:
                    img_size = int((len(img_candidates) / (len(vid_candidates) + len(img_candidates))) * self.data_size)
                    vid_size = self.data_size - img_size
                else:
                    assert self.image_mixing_frac <= 1.0
                    img_size = int(self.image_mixing_frac * self.data_size)
                    vid_size = self.data_size - img_size
            elif self.image_mixing_type == "exclusive":
                assert self.image_mixing_frac >= 0
                img_size = int(self.image_mixing_frac * self.data_size)
                vid_size = self.data_size
            else:
                raise ValueError(f"unsupported image mixing type: {self.image_mixing_type}")

            if distribution == "uniform":
                idx = self.generator.integers(low=0, high=len(img_candidates), size=(img_size,))
                img_candidate_cnts = np.bincount(idx, minlength=len(img_candidates))

                idx = self.generator.integers(low=0, high=len(vid_candidates), size=(vid_size,))
                vid_candidate_cnts = np.bincount(idx, minlength=len(vid_candidates))

            elif distribution == "zipf":
                zipf_alpha = 3.0
                # https://en.wikipedia.org/wiki/Zipf%27s_law#Formal_definition
                ranks = np.power(np.arange(1, len(img_candidates) + 1 + zipf_offset), zipf_alpha)
                H_N_s = np.sum(1 / ranks)
                img_candidate_prob = 1 / (ranks * H_N_s)
                img_candidate_prob = img_candidate_prob[zipf_offset:]
                img_candidate_cnts = img_size * img_candidate_prob / np.sum(img_candidate_prob)

                img_candidate_cnts = np.round(img_candidate_cnts).astype(int)
                if len(img_candidate_cnts) > 0:
                    img_candidate_cnts[0] += img_size - np.sum(img_candidate_cnts)

                ranks = np.power(np.arange(1, len(vid_candidates) + 1 + zipf_offset), zipf_alpha)
                H_N_s = np.sum(1 / ranks)
                vid_candidate_prob = 1 / (ranks * H_N_s)
                vid_candidate_prob = vid_candidate_prob[zipf_offset:]
                vid_candidate_cnts = vid_size * vid_candidate_prob / np.sum(vid_candidate_prob)

                vid_candidate_cnts = np.round(vid_candidate_cnts).astype(int)
                if len(vid_candidate_cnts) > 0:
                    vid_candidate_cnts[0] += vid_size - np.sum(vid_candidate_cnts)
            else:
                raise ValueError(f"unsupported distributionL {distribution}")

            for candidates, candidate_cnts in zip(
                [img_candidates, vid_candidates], [img_candidate_cnts, vid_candidate_cnts]
            ):
                for bucket, cnt in zip(candidates, candidate_cnts):
                    log_str += f"\nbucket: {bucket}, cnt: {cnt}"

                    ar_name, num_frame = bucket
                    height_width_pool = pd.DataFrame(COMMON_AR[ar_name][1].values(), columns=["height", "width"])
                    idx = self.generator.integers(low=0, high=len(height_width_pool), size=(cnt,))
                    bucket_data = height_width_pool.iloc[idx]
                    bucket_data.reset_index(drop=True, inplace=True)
                    data.append(bucket_data)
                    frame_data.extend([num_frame] * cnt)

        data = pd.concat(data, ignore_index=True)
        data.reset_index(drop=True, inplace=True)

        data["num_frames"] = np.array(frame_data)
        data["id"] = np.arange(len(data))
        self.data = data
        log_str += f"\ndefault data_size: {self.data_size}, full data size: {data.shape[0]}"
        logging.info(log_str)
        self.data_size = data.shape[0]

    def __getitem__(self, index):
        if isinstance(index, int):
            # for unit test only
            return self.data.iloc[index]

        # a hack to pass in the (time, height, width) info from sampler
        index, num_frames, height, width, ar_name, sp_size, gas = index
        ar = height / width

        video_fps = 24
        ret = {
            # "id": index,
            "num_frames": num_frames,
            "height": height,
            "width": width,
            "ar": ar,
            "fps": video_fps,
        }
        if not self.preprocessed_data:
            ret["video"] = torch.randn(3, num_frames, height, width, generator=self.torch_generator)
            ret["text"] = "dummy text"
        else:
            nf = 1
            if num_frames > 1:
                nf = num_frames * 5 // 17
            self.torch_generator.manual_seed(self.seed + index + self.data_size)
            ret["video"] = torch.randn(4, nf, height // 8, width // 8, generator=self.torch_generator)
            ret["text"] = torch.rand(1, 300, 4096, generator=self.torch_generator)
            ret["mask"] = torch.ones(300, dtype=torch.long)

        ret["ar_name"] = ar_name
        ret["sp_size"] = sp_size
        ret["gas"] = gas
        return ret

    def __len__(
        self,
    ):
        return self.data_size


class VideoPreProcesssDataset(torch.utils.data.Dataset):
    """load video according to the csv file.

    Args:
        target_video_len (int): the number of video frames will be load.
        align_transform (callable): Align different videos in a specified size.
        temporal_sample (callable): Sample the target length of a video.
    """

    def __init__(
        self,
        data_path=None,
        num_frames=16,
        frame_interval=1,
        image_size=(256, 256),
        transform_name="center",
    ):
        self.data_path = data_path
        self.data = read_file(data_path)
        self.num_frames = num_frames
        self.frame_interval = frame_interval
        self.image_size = image_size
        self.transforms = {
            "image": get_transforms_image(transform_name, image_size),
            "video": get_transforms_video(transform_name, image_size),
        }
        self.data = split_df_by_rank(self.data)

    def get_type(self, path):
        ext = os.path.splitext(path)[-1].lower()
        if ext.lower() in VID_EXTENSIONS:
            return "video"
        else:
            assert ext.lower() in IMG_EXTENSIONS, f"Unsupported file format: {ext}"
            return "image"

    def getitem(self, index):
        sample = self.data.iloc[index]
        path = sample["path"]
        file_type = self.get_type(path)

        if file_type == "video":
            # loading
            vframes, vinfo = read_video(path, backend="av")
            video_fps = vinfo["video_fps"] if "video_fps" in vinfo else 24

            # Sampling video frames
            video = remove_interval(vframes, self.frame_interval)

            # transform
            transform = self.transforms["video"]
            video = transform(video)  # T C H W
        else:
            # loading
            image = pil_loader(path)
            video_fps = IMG_FPS

            # transform
            transform = self.transforms["image"]
            image = transform(image)

            # repeat
            video = image.unsqueeze(0).repeat(self.num_frames, 1, 1, 1)

        # TCHW -> CTHW
        video = video.permute(1, 0, 2, 3)

        ret = {"video": video, "fps": video_fps, "path": path, "index": index}
        return ret

    def __getitem__(self, index):
        for _ in range(10):
            try:
                return self.getitem(index)
            except Exception as e:
                path = self.data.iloc[index]["path"]
                print(f"data {path}: {e}")
                index = np.random.randint(len(self))
        raise RuntimeError("Too many bad data.")

    def __len__(self):
        return len(self.data)


class TextPreProcessDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        data_path=None,
    ):
        self.data_path = data_path
        self.data = read_file(data_path)
        self.data = split_df_by_rank(self.data)

    def __getitem__(self, index):
        sample = self.data.iloc[index]
        ret = {"text": sample["text"], "path": sample["path"], "index": index}
        return ret

    def __len__(self):
        return len(self.data)
