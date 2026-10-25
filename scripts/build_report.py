#!/usr/bin/env python3
"""
Aggregates raw JSON runs under runs/<run_id>/ into runs/<run_id>/summary.json
and docs/data.json for the interactive GitHub Pages dashboard.
"""
import glob
import json
import os
import statistics
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(REPO_ROOT, "runs")
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
SCENES = ["chart", "text-page", "ui-list", "map", "effects"]


def p50(vals):
    return statistics.median(vals) if vals else None


def p99(vals):
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, int(round(0.99 * (len(s) - 1))))
    return s[idx]


def parse_measure_json(path):
    with open(path) as f:
        d = json.load(f)
    samples = d.get("samples", [])
    cpu = [(s["encode_seconds"] + s["submit_seconds"]) * 1000.0 for s in samples]
    gpu = [s["gpu_seconds"] * 1000.0 for s in samples if s.get("gpu_seconds") is not None]
    frm = [
        max(c, g) if g is not None else c
        for c, g in zip(
            cpu,
            [
                s.get("gpu_seconds") * 1000.0 if s.get("gpu_seconds") is not None else None
                for s in samples
            ],
        )
    ]

    energy = d.get("energy") or {}
    rails = energy.get("rails") or {}
    if "S2S_VDD_G3D" in rails:
        gpu_mj = ((rails.get("S2S_VDD_G3D") or {}).get("joules_per_frame") or 0.0) * 1000.0
        cpu_big_mj = ((rails.get("S3M_VDD_CPUCL2") or {}).get("joules_per_frame") or 0.0) * 1000.0
        ddr_mj = sum(
            ((rails.get(k) or {}).get("joules_per_frame") or 0.0) * 1000.0
            for k in ["L21S_VDD2L_MEM", "S4S_VDD2H_MEM", "S5S_VDDQ_MEM", "S1M_VDD_MIF"]
        )
        energy_mj = gpu_mj + cpu_big_mj + ddr_mj
    else:
        jpf = energy.get("joules_per_frame")
        if jpf is None and "process" in rails:
            jpf = (rails["process"] or {}).get("joules_per_frame")
        if jpf is None and energy.get("task_joules") is not None and samples:
            jpf = energy["task_joules"] / len(samples)
        energy_mj = (jpf or 0.0) * 1000.0
        gpu_mj = None

    steady = (d.get("memory") or {}).get("steady") or {}
    proc = steady.get("process") or {}
    phys_mib = (
        ((proc.get("phys_footprint_bytes") or {}).get("measured") or 0)
        or ((proc.get("pss_bytes") or {}).get("measured") or 0)
    ) / (1024.0 * 1024.0)
    gpu_mem_mib = (
        ((proc.get("metal_allocated_bytes") or {}).get("measured") or 0)
        or ((proc.get("gpu_mem_bytes") or {}).get("measured") or 0)
    ) / (1024.0 * 1024.0)

    return {
        "prepare_ms": (d.get("prepare_seconds") or 0.0) * 1000.0,
        "cpu_p50_ms": p50(cpu),
        "gpu_p50_ms": p50(gpu),
        "gpu_p99_ms": p99(gpu),
        "frame_p50_ms": p50(frm),
        "frame_p99_ms": p99(frm),
        "energy_mj": energy_mj,
        "gpu_rail_mj": gpu_mj,
        "process_mem_mib": phys_mib,
        "driver_gpu_mem_mib": gpu_mem_mib,
    }


def aggregate_run(run_dir):
    prov_path = os.path.join(run_dir, "provenance.json")
    prov = json.load(open(prov_path)) if os.path.exists(prov_path) else {}
    out = {"provenance": prov, "devices": {}}

    # iPad Pro M4
    ipad_dir = os.path.join(run_dir, "ipad-pro-m4")
    if os.path.isdir(ipad_dir):
        ipad_data = {"gpu": {}, "cpu": {}}
        for sc in SCENES:
            ipad_data["gpu"][sc] = {}
            for eng in ["cherenkov", "skia-metal", "vello-classic", "vello-hybrid"]:
                rounds = [
                    parse_measure_json(os.path.join(ipad_dir, "gpu", f"r{r}", f"{sc}-{eng}", "measure.json"))
                    for r in range(3)
                ]
                mem_path = os.path.join(ipad_dir, "metal-mem", f"{sc}-{eng}", "measure.json")
                metal_mib = parse_measure_json(mem_path)["driver_gpu_mem_mib"] if os.path.exists(mem_path) else 0.0
                ipad_data["gpu"][sc][eng] = {
                    "gpu_p50_ms": statistics.median([x["gpu_p50_ms"] for x in rounds]),
                    "gpu_p99_ms": statistics.median([x["gpu_p99_ms"] for x in rounds]),
                    "cpu_p50_ms": statistics.median([x["cpu_p50_ms"] for x in rounds]),
                    "frame_p50_ms": statistics.median([x["frame_p50_ms"] for x in rounds]),
                    "frame_p99_ms": statistics.median([x["frame_p99_ms"] for x in rounds]),
                    "energy_mj": statistics.median([x["energy_mj"] for x in rounds]),
                    "process_mem_mib": statistics.median([x["process_mem_mib"] for x in rounds]),
                    "driver_gpu_mem_mib": metal_mib,
                }
            ipad_data["cpu"][sc] = {}
            for eng in ["cherenkov-cpu", "skia-cpu", "vello-cpu"]:
                rounds = [
                    parse_measure_json(os.path.join(ipad_dir, "cpu", f"r{r}", f"{sc}-{eng}", "measure.json"))
                    for r in range(3)
                ]
                ipad_data["cpu"][sc][eng] = {
                    "frame_p50_ms": statistics.median([x["frame_p50_ms"] for x in rounds]),
                    "frame_p99_ms": statistics.median([x["frame_p99_ms"] for x in rounds]),
                    "energy_mj": statistics.median([x["energy_mj"] for x in rounds]),
                    "process_mem_mib": statistics.median([x["process_mem_mib"] for x in rounds]),
                }
        out["devices"]["ipad-pro-m4"] = ipad_data

    # Pixel 9 Pro
    p9_dir = os.path.join(run_dir, "pixel-9-pro")
    if os.path.isdir(p9_dir):
        p9_data = {"gpu": {}, "cpu": {}}
        for sc in SCENES:
            p9_data["gpu"][sc] = {}
            for eng in ["cherenkov", "skia-vulkan", "vello-classic", "vello-hybrid"]:
                rounds = [
                    parse_measure_json(os.path.join(p9_dir, f"gpu_r{r}_{sc}_{eng}.json"))
                    for r in range(3)
                ]
                p9_data["gpu"][sc][eng] = {
                    "gpu_p50_ms": statistics.median([x["gpu_p50_ms"] for x in rounds]),
                    "gpu_p99_ms": statistics.median([x["gpu_p99_ms"] for x in rounds]),
                    "cpu_p50_ms": statistics.median([x["cpu_p50_ms"] for x in rounds]),
                    "frame_p50_ms": statistics.median([x["frame_p50_ms"] for x in rounds]),
                    "frame_p99_ms": statistics.median([x["frame_p99_ms"] for x in rounds]),
                    "energy_mj": statistics.median([x["energy_mj"] for x in rounds]),
                    "gpu_rail_mj": statistics.median([x["gpu_rail_mj"] for x in rounds]),
                    "process_mem_mib": statistics.median([x["process_mem_mib"] for x in rounds]),
                    "driver_gpu_mem_mib": statistics.median([x["driver_gpu_mem_mib"] for x in rounds]),
                }
            p9_data["cpu"][sc] = {}
            for eng in ["cherenkov-cpu", "skia-cpu", "vello-cpu"]:
                rounds = [
                    parse_measure_json(os.path.join(p9_dir, f"cpu_r{r}_{sc}_{eng}.json"))
                    for r in range(3)
                ]
                p9_data["cpu"][sc][eng] = {
                    "frame_p50_ms": statistics.median([x["frame_p50_ms"] for x in rounds]),
                    "frame_p99_ms": statistics.median([x["frame_p99_ms"] for x in rounds]),
                    "energy_mj": statistics.median([x["energy_mj"] for x in rounds]),
                    "process_mem_mib": statistics.median([x["process_mem_mib"] for x in rounds]),
                }
        out["devices"]["pixel-9-pro"] = p9_data

    return out


def render_svgs(latest_run, assets_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(assets_dir, exist_ok=True)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#F8FAFC",
        "axes.edgecolor": "#CBD5E1",
        "axes.grid": True,
        "grid.color": "#E2E8F0",
        "grid.linestyle": "--",
        "grid.alpha": 0.8,
    })

    colors_gpu = {
        "cherenkov": "#2563EB",
        "skia-metal": "#10B981",
        "skia-vulkan": "#10B981",
        "vello-classic": "#F59E0B",
        "vello-hybrid": "#8B5CF6",
    }
    colors_cpu = {
        "cherenkov-cpu": "#2563EB",
        "skia-cpu": "#10B981",
        "vello-cpu": "#F59E0B",
    }

    x = np.arange(len(SCENES))
    width = 0.20
    width_cpu = 0.25

    for dev_key, dev_title in [
        ("ipad-pro-m4", "iPad Pro 13-inch (M4 Metal, 2752×2064 @ 120 Hz)"),
        ("pixel-9-pro", "Pixel 9 Pro (Tensor G4 / Mali-G715 Vulkan, 960×2142 @ 120 Hz)"),
    ]:
        if dev_key not in latest_run["devices"]:
            continue
        dev = latest_run["devices"][dev_key]
        gpu_engines = list(dev["gpu"]["chart"].keys())
        cpu_engines = list(dev["cpu"]["chart"].keys())

        # GPU Timing SVG
        fig, axes = plt.subplots(1, 2, figsize=(15, 5.6))
        fig.suptitle(f"{dev_title} — GPU & Frame Timing (Lower is Better)", fontsize=14, fontweight="bold", y=0.98)
        for idx, (mkey, mtitle) in enumerate([
            ("gpu_p50_ms", "GPU Execution Time p50 (ms)"),
            ("frame_p50_ms", "Total Frame Time p50 — max(CPU, GPU) (ms)"),
        ]):
            ax = axes[idx]
            for i, eng in enumerate(gpu_engines):
                vals = [dev["gpu"][sc][eng][mkey] for sc in SCENES]
                bars = ax.bar(x + (i - 1.5) * width, vals, width, label=eng, color=colors_gpu[eng], edgecolor="white", linewidth=0.6, zorder=3)
                for bar, v in zip(bars, vals):
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (max(vals) * 0.02), f"{v:.2f}" if v < 10 else f"{v:.1f}", ha="center", va="bottom", fontsize=7.5, fontweight="bold")
            ax.axhline(8.333, color="#EF4444", linestyle=":", linewidth=1.4, label="120 Hz Budget (8.33 ms)", zorder=4)
            ax.set_title(mtitle, pad=10)
            ax.set_ylabel("Milliseconds (ms)")
            ax.set_xticks(x)
            ax.set_xticklabels(SCENES, fontweight="bold")
            ax.set_ylim(0, max(12, ax.get_ylim()[1] * 1.15))
            ax.legend(frameon=True, facecolor="white", edgecolor="#CBD5E1", fontsize=8.5, loc="upper left")
        plt.tight_layout(rect=[0, 0, 1, 0.93])
        plt.savefig(os.path.join(assets_dir, f"{dev_key}_gpu_timing.svg"))
        plt.close()

        # GPU Energy & Memory SVG
        fig, axes = plt.subplots(1, 3, figsize=(19.5, 5.8))
        fig.suptitle(f"{dev_title} — Energy & Unified Driver GPU Memory", fontsize=14, fontweight="bold", y=0.98)
        for idx, (mkey, mtitle, ylabel, fmt) in enumerate([
            ("energy_mj", "Energy per Frame", "mJ / frame", "{:.2f}"),
            ("driver_gpu_mem_mib", "Driver GPU Memory (MTLDevice / dumpsys gpumem)", "MiB (Steady)", "{:.1f}"),
            ("process_mem_mib", "Process Memory (phys_footprint / PSS)", "MiB (Steady)", "{:.1f}"),
        ]):
            ax = axes[idx]
            all_vals = []
            for i, eng in enumerate(gpu_engines):
                vals = [dev["gpu"][sc][eng][mkey] for sc in SCENES]
                all_vals.extend(vals)
            panel_max = max(all_vals) if all_vals else 1.0
            for i, eng in enumerate(gpu_engines):
                vals = [dev["gpu"][sc][eng][mkey] for sc in SCENES]
                bars = ax.bar(x + (i - 1.5) * width, vals, width, label=eng, color=colors_gpu[eng], edgecolor="white", linewidth=0.6, zorder=3)
                for bar, v in zip(bars, vals):
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + (panel_max * 0.025),
                        fmt.format(v),
                        ha="center",
                        va="bottom",
                        fontsize=6.8,
                        fontweight="bold",
                        rotation=25 if panel_max >= 100 else 0,
                    )
            ax.set_title(mtitle, pad=10)
            ax.set_ylabel(ylabel)
            ax.set_xticks(x)
            ax.set_xticklabels(SCENES, fontweight="bold")
            ax.set_ylim(0, panel_max * 1.32)
            ax.legend(frameon=True, facecolor="white", edgecolor="#CBD5E1", fontsize=7.8, loc="upper left", ncol=2)
        plt.tight_layout(rect=[0, 0, 1, 0.93])
        plt.savefig(os.path.join(assets_dir, f"{dev_key}_gpu_energy_memory.svg"))
        plt.close()

        # CPU Rasterizer SVG
        fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.4))
        fig.suptitle(f"{dev_title} — CPU Rasterizers at Scene Resolution", fontsize=14, fontweight="bold", y=0.98)
        for idx, (mkey, mtitle, ylabel, fmt) in enumerate([
            ("frame_p50_ms", "CPU Frame Time p50 (ms)", "Milliseconds (ms)", "{:.1f}"),
            ("process_mem_mib", "Process Memory (MiB)", "MiB (Steady)", "{:.1f}"),
        ]):
            ax = axes[idx]
            for i, eng in enumerate(cpu_engines):
                vals = [dev["cpu"][sc][eng][mkey] for sc in SCENES]
                bars = ax.bar(x + (i - 1.0) * width_cpu, vals, width_cpu, label=eng, color=colors_cpu[eng], edgecolor="white", linewidth=0.6, zorder=3)
                for bar, v in zip(bars, vals):
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (max(vals) * 0.02), fmt.format(v), ha="center", va="bottom", fontsize=8, fontweight="bold")
            ax.set_title(mtitle, pad=10)
            ax.set_ylabel(ylabel)
            ax.set_xticks(x)
            ax.set_xticklabels(SCENES, fontweight="bold")
            ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
            ax.legend(frameon=True, facecolor="white", edgecolor="#CBD5E1", fontsize=8.5, loc="upper left")
        plt.tight_layout(rect=[0, 0, 1, 0.93])
        plt.savefig(os.path.join(assets_dir, f"{dev_key}_cpu_comparison.svg"))
        plt.close()


def main():
    import sys
    all_runs = []
    for entry in sorted(os.listdir(RUNS_DIR)):
        rdir = os.path.join(RUNS_DIR, entry)
        if os.path.isfile(os.path.join(rdir, "provenance.json")):
            all_runs.append(aggregate_run(rdir))
    history = []
    for path in sorted((Path(REPO_ROOT) / "statistics").glob("*.jsonl")):
        history.extend(json.loads(line) for line in path.read_text().splitlines())
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "data.json"), "w") as f:
        json.dump({"runs": all_runs, "history": history}, f, separators=(",", ":"))
    print(f"Aggregated {len(all_runs)} run(s) into docs/data.json")
    if "--svg" in sys.argv and all_runs:
        assets_dir = os.path.join(DOCS_DIR, "assets")
        render_svgs(all_runs[-1], assets_dir)
        print(f"Generated SVG charts in {assets_dir}")


if __name__ == "__main__":
    main()
