#!/usr/bin/env python3
import json
import os
import subprocess
import time

from sync_runs import RunArchive, runner_arguments
from archive import summarize

SCENES = ["chart", "text-page", "ui-list", "map", "effects"]
GPU_ENGINES = ["cherenkov", "skia-vulkan", "vello-classic", "vello-hybrid"]
CPU_ENGINES = ["cherenkov-cpu", "skia-cpu", "vello-cpu"]
ROUNDS = 3


def run_adb_cmd(adb, cmd_str: str) -> str:
    res = subprocess.run(
        adb + ["shell", f"su -c '{cmd_str}'"],
        capture_output=True,
        text=True,
        check=True, timeout=300,
    )
    return res.stdout


def run_cell(engine: str, scene: str, round_idx: int, is_gpu: bool, options, journal):
    adb = ["adb", "-s", options.device]
    tag = "gpu" if is_gpu else "cpu"
    out_file = os.path.join(options.output, f"{tag}_r{round_idx}_{scene}_{engine}.json")
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

    configuration = {"arguments": cmd.split()[1:-2]}
    if os.path.exists(out_file):
        journal.record(out_file, configuration)
        return

    t0 = time.time()
    print(f"[RUN] r{round_idx} {scene} / {engine} ({tag}) ...", flush=True)
    run_adb_cmd(adb, cmd)
    subprocess.run(adb + ["pull", remote_json, out_file], capture_output=True, check=True, timeout=60)
    run_adb_cmd(adb, f"rm -f {remote_json}")

    journal.record(out_file, configuration)
    with open(out_file) as f:
        data = json.load(f)
    timing = summarize(data, True)["timing_ms"]
    gpu_p50 = timing["gpu"]["p50"] if timing["gpu"] else None
    frame_p50 = timing["frame"]["p50"]
    dt = time.time() - t0
    if gpu_p50 is not None:
        print(
            f"  -> done in {dt:.1f}s | gpu_p50={gpu_p50:.3f}ms frame_p50={frame_p50:.3f}ms",
            flush=True,
        )
    else:
        print(
            f"  -> done in {dt:.1f}s | frame_p50={frame_p50:.3f}ms",
            flush=True,
        )


def main():
    options = runner_arguments("Run and automatically archive Pixel hardware benchmarks").parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(options.metadata.read_text())
    properties = {}
    for key in ("ro.product.model", "ro.product.device", "ro.build.version.release", "ro.build.display.id"):
        properties[key] = subprocess.check_output(
            ["adb", "-s", options.device, "shell", "getprop", key], text=True, timeout=15).strip()
    if metadata["device"]["model_identifier"] != properties["ro.product.device"]:
        raise ValueError("connected Android device does not match the requested model identifier")
    metadata["device"].update(model=properties["ro.product.model"],
                              os=f'Android {properties["ro.build.version.release"]} ({properties["ro.build.display.id"]})')
    journal = RunArchive(options.output / "archive", metadata)
    try:
        measure(options, journal)
    finally:
        journal.publish(options.repository)


def measure(options, journal):
    print("=== Running GPU Native (960x2142 @ 120Hz) Interleaved Rounds ===")
    for r in range(ROUNDS):
        engines = GPU_ENGINES if (r % 2 == 0) else list(reversed(GPU_ENGINES))
        scenes = SCENES if (r % 2 == 0) else list(reversed(SCENES))
        for scene in scenes:
            for engine in engines:
                run_cell(engine, scene, r, is_gpu=True, options=options, journal=journal)

    print("=== Running CPU (Scene Resolution) Interleaved Rounds ===")
    for r in range(ROUNDS):
        engines = CPU_ENGINES if (r % 2 == 0) else list(reversed(CPU_ENGINES))
        scenes = SCENES if (r % 2 == 0) else list(reversed(SCENES))
        for scene in scenes:
            for engine in engines:
                run_cell(engine, scene, r, is_gpu=False, options=options, journal=journal)

    print("=== All 105 Pixel 9 Pro benchmark runs completed! ===")


if __name__ == "__main__":
    main()
