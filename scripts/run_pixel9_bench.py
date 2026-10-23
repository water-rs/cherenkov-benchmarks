#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

ADB = ["adb", "-s", "100.76.86.48:5555"]
OUT_DIR = "/tmp/pixel-9-pro-bench-e11b54dcf"
os.makedirs(OUT_DIR, exist_ok=True)

SCENES = ["chart", "text-page", "ui-list", "map", "effects"]
GPU_ENGINES = ["cherenkov", "skia-vulkan", "vello-classic", "vello-hybrid"]
CPU_ENGINES = ["cherenkov-cpu", "skia-cpu", "vello-cpu"]
ROUNDS = 3


def run_adb_cmd(cmd_str: str) -> str:
    res = subprocess.run(
        ADB + ["shell", f"su -c '{cmd_str}'"],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def run_cell(engine: str, scene: str, round_idx: int, is_gpu: bool):
    tag = "gpu" if is_gpu else "cpu"
    out_file = os.path.join(OUT_DIR, f"{tag}_r{round_idx}_{scene}_{engine}.json")
    if os.path.exists(out_file) and os.path.getsize(out_file) > 500:
        print(f"[SKIP] {out_file} already exists")
        return

    remote_json = f"/data/local/tmp/bench_out_{tag}_r{round_idx}_{scene}_{engine}.json"
    if is_gpu:
        cmd = (
            f"/data/local/tmp/cherenkov-bench measure "
            f"--engine {engine} "
            f"--scene /data/local/tmp/scenes/perf/{scene} "
            f"--native 960x2142 --rate 120 --warmup 30 --frames 120 --cpu 7 --energy "
            f"--out {remote_json}"
        )
    else:
        cmd = (
            f"/data/local/tmp/cherenkov-bench measure "
            f"--engine {engine} "
            f"--scene /data/local/tmp/scenes/perf/{scene} "
            f"--warmup 10 --frames 30 --cpu 7 --energy "
            f"--out {remote_json}"
        )

    t0 = time.time()
    print(f"[RUN] r{round_idx} {scene} / {engine} ({tag}) ...", flush=True)
    run_adb_cmd(cmd)
    subprocess.run(ADB + ["pull", remote_json, out_file], capture_output=True, check=True)
    run_adb_cmd(f"rm -f {remote_json}")

    with open(out_file) as f:
        data = json.load(f)
    summary = data.get("summary", {})
    gpu_p50 = summary.get("gpu_p50")
    encode_p50 = summary.get("encode_p50", 0.0)
    submit_p50 = summary.get("submit_p50", 0.0)
    cpu_p50 = encode_p50 + submit_p50
    frame_p50 = max(cpu_p50, gpu_p50) if gpu_p50 is not None else cpu_p50
    dt = time.time() - t0
    if gpu_p50 is not None:
        print(
            f"  -> done in {dt:.1f}s | gpu_p50={gpu_p50*1000:.3f}ms frame_p50={frame_p50*1000:.3f}ms",
            flush=True,
        )
    else:
        print(
            f"  -> done in {dt:.1f}s | frame_p50={frame_p50*1000:.3f}ms",
            flush=True,
        )


def main():
    print("=== Running GPU Native (960x2142 @ 120Hz) Interleaved Rounds ===")
    for r in range(ROUNDS):
        engines = GPU_ENGINES if (r % 2 == 0) else list(reversed(GPU_ENGINES))
        scenes = SCENES if (r % 2 == 0) else list(reversed(SCENES))
        for scene in scenes:
            for engine in engines:
                run_cell(engine, scene, r, is_gpu=True)

    print("=== Running CPU (Scene Resolution) Interleaved Rounds ===")
    for r in range(ROUNDS):
        engines = CPU_ENGINES if (r % 2 == 0) else list(reversed(CPU_ENGINES))
        scenes = SCENES if (r % 2 == 0) else list(reversed(SCENES))
        for scene in scenes:
            for engine in engines:
                run_cell(engine, scene, r, is_gpu=False)

    print("=== All 105 Pixel 9 Pro benchmark runs completed! ===")


if __name__ == "__main__":
    main()
