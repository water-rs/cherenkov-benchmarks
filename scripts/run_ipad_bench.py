#!/usr/bin/env python3
import json
import pathlib
import shutil
import subprocess
import sys
import time

UDID = "00008132-000228C80239001C"
BUNDLE = "dev.cherenkov.bench"
OUT_ROOT = pathlib.Path("/tmp/ipad-m4-bench-e11b54dcf")

SCENES = ["chart", "text-page", "ui-list", "map", "effects"]
GPU_ENGINES = ["cherenkov", "skia-metal", "vello-classic", "vello-hybrid"]
CPU_ENGINES = ["cherenkov-cpu", "skia-cpu", "vello-cpu"]


def run_cmd(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def copy_to(src: pathlib.Path, dst: str):
    res = run_cmd([
        "xcrun", "devicectl", "device", "copy", "to",
        "--device", UDID,
        "--domain-type", "appDataContainer",
        "--domain-identifier", BUNDLE,
        "--source", str(src),
        "--destination", dst,
    ])
    if res.returncode != 0:
        raise RuntimeError(f"copy to {dst} failed: {res.stderr or res.stdout}")


def copy_from(src: str, dst: pathlib.Path):
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = run_cmd([
        "xcrun", "devicectl", "device", "copy", "from",
        "--device", UDID,
        "--domain-type", "appDataContainer",
        "--domain-identifier", BUNDLE,
        "--source", src,
        "--destination", str(dst),
    ])
    if res.returncode != 0:
        raise RuntimeError(f"copy from {src} failed: {res.stderr or res.stdout}")


def run_one_launch(run_id: str, args_list: list[list[str]], dest_dir: pathlib.Path, timeout=240):
    if (dest_dir / "done.json").is_file():
        done = json.loads((dest_dir / "done.json").read_text())
        if done.get("run_id") == run_id and all(r.get("exit_code") == 0 for r in done.get("results", [])):
            print(f"[skip] {run_id} already complete", flush=True)
            return done

    args_file = OUT_ROOT / "tmp_bench_args.json"
    args_file.parent.mkdir(parents=True, exist_ok=True)
    args_file.write_text(json.dumps({"run_id": run_id, "runs": args_list}))
    copy_to(args_file, "Documents/bench-args.json")

    t0 = time.monotonic()
    res = run_cmd([
        "xcrun", "devicectl", "device", "process", "launch",
        "--terminate-existing", "--console",
        "--device", UDID,
        BUNDLE,
        "--exit-after-run",
    ], timeout=timeout)
    elapsed = time.monotonic() - t0
    if res.returncode != 0:
        print(f"[warn] launch {run_id} returned {res.returncode}: {res.stderr or res.stdout}", flush=True)

    copy_from("Documents/out", dest_dir)
    done_path = dest_dir / "done.json"
    if not done_path.is_file():
        raise RuntimeError(f"missing done.json for {run_id}")
    done = json.loads(done_path.read_text())
    results = done.get("results", [])
    codes = [r.get("exit_code") for r in results]
    thermals = [f"{r.get('thermal_before')}->{r.get('thermal_after')}" for r in results]
    print(f"[done] {run_id} in {elapsed:.1f}s codes={codes} thermal={thermals}", flush=True)
    return done


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    if mode in ("all", "gpu"):
        print("=== Running GPU Native (2752x2064 @ 120Hz, 3 interleaved rounds) ===", flush=True)
        for round_idx in range(3):
            engines = GPU_ENGINES if round_idx % 2 == 0 else list(reversed(GPU_ENGINES))
            for scene in SCENES:
                for engine in engines:
                    run_id = f"gpu-r{round_idx}-{scene}-{engine}"
                    dest_dir = OUT_ROOT / "gpu" / f"r{round_idx}" / f"{scene}-{engine}"
                    args = [[
                        "measure",
                        "--engine", engine,
                        "--scene", f"Documents/scenes/perf/{scene}",
                        "--native", "2752x2064",
                        "--rate", "120",
                        "--energy",
                        "--warmup", "30",
                        "--frames", "120",
                        "--out", "Documents/out/measure.json",
                    ]]
                    run_one_launch(run_id, args, dest_dir)

    if mode in ("all", "cpu"):
        print("=== Running CPU (scene resolution, 3 interleaved rounds) ===", flush=True)
        for round_idx in range(3):
            engines = CPU_ENGINES if round_idx % 2 == 0 else list(reversed(CPU_ENGINES))
            for scene in SCENES:
                for engine in engines:
                    run_id = f"cpu-r{round_idx}-{scene}-{engine}"
                    dest_dir = OUT_ROOT / "cpu" / f"r{round_idx}" / f"{scene}-{engine}"
                    frames = "30" if scene in ("map", "effects") else "60"
                    warmup = "5" if scene in ("map", "effects") else "10"
                    args = [[
                        "measure",
                        "--engine", engine,
                        "--scene", f"Documents/scenes/perf/{scene}",
                        "--energy",
                        "--warmup", warmup,
                        "--frames", frames,
                        "--out", "Documents/out/measure.json",
                    ]]
                    run_one_launch(run_id, args, dest_dir, timeout=300)


if __name__ == "__main__":
    main()
