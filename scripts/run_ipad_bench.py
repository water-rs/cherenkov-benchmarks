#!/usr/bin/env python3
import json
import pathlib
import shutil
import subprocess
import tempfile
import time

from sync_runs import RunArchive, runner_arguments

BUNDLE = "dev.cherenkov.bench"
SCENES = ["chart", "text-page", "ui-list", "map", "effects"]
GPU_ENGINES = ["cherenkov", "skia-metal", "vello-classic", "vello-hybrid"]
CPU_ENGINES = ["cherenkov-cpu", "skia-cpu", "vello-cpu"]


def run_cmd(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def copy_to(src: pathlib.Path, dst: str, device):
    res = run_cmd([
        "xcrun", "devicectl", "device", "copy", "to",
        "--device", device,
        "--domain-type", "appDataContainer",
        "--domain-identifier", BUNDLE,
        "--source", str(src),
        "--destination", dst,
    ])
    if res.returncode != 0:
        raise RuntimeError(f"copy to {dst} failed: {res.stderr or res.stdout}")


def copy_from(src: str, dst: pathlib.Path, device):
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = run_cmd([
        "xcrun", "devicectl", "device", "copy", "from",
        "--device", device,
        "--domain-type", "appDataContainer",
        "--domain-identifier", BUNDLE,
        "--source", src,
        "--destination", str(dst),
    ])
    if res.returncode != 0:
        raise RuntimeError(f"copy from {src} failed: {res.stderr or res.stdout}")


def run_one_launch(run_id: str, args_list: list[list[str]], dest_dir: pathlib.Path, device, out_root, journal, timeout=240):
    if (dest_dir / "done.json").is_file():
        done = json.loads((dest_dir / "done.json").read_text())
        if (done.get("run_id") == run_id and len(done.get("results", [])) == len(args_list)
                and all(r.get("exit_code") == 0 for r in done["results"])):
            print(f"[skip] {run_id} already complete", flush=True)
            journal.record(dest_dir / "measure.json", {"arguments": args_list},
                           valid=True, completion=done)
            return done

    args_file = out_root / "tmp_bench_args.json"
    args_file.parent.mkdir(parents=True, exist_ok=True)
    args_file.write_text(json.dumps({"run_id": run_id, "runs": args_list}))
    copy_to(args_file, "Documents/bench-args.json", device)

    t0 = time.monotonic()
    res = run_cmd([
        "xcrun", "devicectl", "device", "process", "launch",
        "--terminate-existing", "--console",
        "--device", device,
        BUNDLE,
        "--exit-after-run",
    ], timeout=timeout)
    elapsed = time.monotonic() - t0
    if res.returncode != 0:
        print(f"[warn] launch {run_id} returned {res.returncode}: {res.stderr or res.stdout}", flush=True)

    copy_from("Documents/out", dest_dir, device)
    done_path = dest_dir / "done.json"
    if not done_path.is_file():
        raise RuntimeError(f"missing done.json for {run_id}")
    done = json.loads(done_path.read_text())
    results = done.get("results", [])
    codes = [r.get("exit_code") for r in results]
    thermals = [f"{r.get('thermal_before')}->{r.get('thermal_after')}" for r in results]
    print(f"[done] {run_id} in {elapsed:.1f}s codes={codes} thermal={thermals}", flush=True)
    if done.get("run_id") != run_id or not results:
        raise RuntimeError(f"unexpected completion report for {run_id}")
    journal.record(dest_dir / "measure.json", {"arguments": args_list},
                   valid=all(code == 0 for code in codes), completion=done)
    if any(code != 0 for code in codes):
        raise RuntimeError(f"benchmark failed: {run_id}: {codes}")
    return done


def main():
    parser = runner_arguments("Run and automatically archive iPad hardware benchmarks")
    parser.add_argument("--mode", choices=("all", "gpu", "cpu"), default="all")
    options = parser.parse_args()
    metadata = json.loads(options.metadata.read_text())
    with tempfile.TemporaryDirectory() as directory:
        details = pathlib.Path(directory) / "device.json"
        result = run_cmd(["xcrun", "devicectl", "device", "info", "details", "--device",
                          options.device, "--json-output", str(details)], timeout=30)
        result.check_returncode()
        device = json.loads(details.read_text())["result"]
    hardware, software = device["hardwareProperties"], device["deviceProperties"]
    if metadata["device"]["model_identifier"] != hardware["productType"]:
        raise ValueError("connected Apple device does not match the requested model identifier")
    metadata["device"].update(model=hardware["marketingName"],
                              os=f'{hardware["platform"]} {software["osVersionNumber"]} ({software["osBuildUpdate"]})')
    journal = RunArchive(options.output / "archive", metadata)
    try:
        measure(options, journal)
    finally:
        journal.publish(options.repository)


def measure(options, journal):
    mode = options.mode
    out_root = options.output
    out_root.mkdir(parents=True, exist_ok=True)

    if mode in ("all", "gpu"):
        print("=== Running GPU Native (2752x2064 @ 120Hz, 3 interleaved rounds) ===", flush=True)
        for round_idx in range(3):
            engines = GPU_ENGINES if round_idx % 2 == 0 else list(reversed(GPU_ENGINES))
            for scene in SCENES:
                for engine in engines:
                    run_id = f"gpu-r{round_idx}-{scene}-{engine}"
                    dest_dir = out_root / "gpu" / f"r{round_idx}" / f"{scene}-{engine}"
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
                    run_one_launch(run_id, args, dest_dir, options.device, out_root, journal)

    if mode in ("all", "cpu"):
        print("=== Running CPU (scene resolution, 3 interleaved rounds) ===", flush=True)
        for round_idx in range(3):
            engines = CPU_ENGINES if round_idx % 2 == 0 else list(reversed(CPU_ENGINES))
            for scene in SCENES:
                for engine in engines:
                    run_id = f"cpu-r{round_idx}-{scene}-{engine}"
                    dest_dir = out_root / "cpu" / f"r{round_idx}" / f"{scene}-{engine}"
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
                    run_one_launch(run_id, args, dest_dir, options.device, out_root, journal, timeout=300)


if __name__ == "__main__":
    main()
