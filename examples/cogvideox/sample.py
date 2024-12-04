from videosys import CogVideoXConfig, VideoSysEngine
import argparse
import time 

def run_base():
    # models: "THUDM/CogVideoX-2b" or "THUDM/CogVideoX-5b"
    # change num_gpus for multi-gpu inference
    # config = CogVideoXConfig("THUDM/CogVideoX-2b", num_gpus=1)
    config = CogVideoXConfig("/home/pod/shared-nvme/CogVideoX-2b", num_gpus=1)

    engine = VideoSysEngine(config)

    prompt = "Sunset over the sea."
    # num frames should be <= 49. resolution is fixed to 720p.
    # seed=-1 means random seed. >0 means fixed seed.
    seed = 10 #这里可能会影响视频生成效果
    start = time.time()
    video = engine.generate(
        prompt=prompt,
        guidance_scale=6,
        num_inference_steps=50,
        num_frames=49,
        seed=seed,
    ).video[0]
    end = time.time()
    print(f"CogVideoX-2b run_base generate video cost time:{end-start}")
    engine.save_video(video, f"./outputs/{prompt}-{seed}.mp4")


def run_pab():
    config = CogVideoXConfig("/home/pod/shared-nvme/CogVideoX-2b", enable_pab=True,num_gpus=1)
    engine = VideoSysEngine(config)

    prompt = "Sunset over the sea."
    start = time.time()
    video = engine.generate(prompt).video[0]
    end = time.time()
    print(f"CogVideoX-2b run_pab generate video cost time:{end-start}")
    engine.save_video(video, f"./outputs/{prompt}-pab.mp4")


def run_low_mem():
    config = CogVideoXConfig("/home/pod/shared-nvme/CogVideoX-2b", cpu_offload=True, vae_tiling=True)
    engine = VideoSysEngine(config)

    prompt = "Sunset over the sea."
    start = time.time()
    video = engine.generate(prompt).video[0]
    end = time.time()
    print(f"CogVideoX-2b run_low_mem generate video cost time:{end-start}")
    engine.save_video(video, f"./outputs/{prompt}-low_mem.mp4")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--base',action='store_true')
    parser.add_argument('--pab',action='store_true')
    parser.add_argument('--low_mem',action='store_true')
    args = parser.parse_args()

    if args.base:
        run_base()
    elif args.pab:
        run_pab()
    else:
        run_low_mem()
