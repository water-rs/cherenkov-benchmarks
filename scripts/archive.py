#!/usr/bin/env python3
"""Archive independent hardware measurements and append yearly statistics."""

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def quantiles(values):
    values = sorted(values)
    if not values:
        return None
    if not all(math.isfinite(v) for v in values):
        raise ValueError("measurement contains non-finite samples")
    return {"p50": values[math.ceil(len(values) * 0.5) - 1],
            "p99": values[math.ceil(len(values) * 0.99) - 1]}


def timing_samples(report):
    """Milliseconds for measured intervals; unavailable intervals stay absent."""
    samples = report["samples"]
    result = {"cpu": [(s["encode_seconds"] + s["submit_seconds"]) * 1000 for s in samples]}
    for metric, field in (("gpu", "gpu_seconds"), ("latency", "latency_seconds"),
                          ("gpu_wait", "gpu_wait_seconds")):
        values = [sample.get(field) for sample in samples]
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError(f"{metric} measurements are missing from only some frames")
        result[metric] = [value * 1000 for value in values if value is not None]
    return result


def summarize(report, valid):
    if not valid:
        return None
    samples = report["samples"]
    if not samples:
        raise ValueError("a valid measurement must contain frames")
    timing = timing_samples(report)
    counters = {}
    if samples[0].get("gpu_counters") is not None:
        for key in samples[0]["gpu_counters"]:
            counters[key] = quantiles([s["gpu_counters"][key] for s in samples])
    return {"frames": len(samples),
            "timing_ms": {metric: quantiles(values) for metric, values in timing.items()},
            "gpu_timing_unavailable": report.get("gpu_timing_unavailable"),
            "gpu_counters": counters, "energy": report.get("energy"),
            "memory": report.get("memory")}



def validate_identity(metadata):
    device = metadata["device"]
    for key in ("model", "model_identifier", "os", "gpu"):
        if not isinstance(device[key], str) or not device[key].strip():
            raise ValueError(f"device.{key} is required")
    revision = metadata["source"]["revision"]
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("source.revision must be a full commit hash")


def archive(root, report, metadata):
    """Write one immutable raw JSON and one idempotent yearly JSONL record."""
    validate_identity(metadata)
    timestamp = dt.datetime.fromisoformat(metadata["recorded_at"])
    if timestamp.tzinfo is None:
        raise ValueError("recorded_at must carry a timezone")
    timestamp = timestamp.astimezone(ZoneInfo("America/New_York"))
    device = metadata["device"]
    if not isinstance(metadata["valid"], bool):
        raise ValueError("valid must be a boolean")
    metadata = dict(metadata, recorded_at=timestamp.isoformat())
    # Configuration is explicit: fixed clocks and governor runs are not pooled.
    configuration = metadata["configuration"]
    comparison = {"device": device, "engine": report["engine"],
                  "scene": report["scene"], "width": report["width"],
                  "height": report["height"], "configuration": configuration,
                  "wait_for_completion": report.get("wait_for_completion"),
                  "adapter": {key: value for key, value in report.get("device", {}).items()
                              if key in ("adapter", "backend", "driver", "driver_info", "target_format", "cpu")}}
    raw = {"schema_version": 1, "metadata": metadata, "report": report}
    run_id = hashlib.sha256(encoded(raw).encode()).hexdigest()
    model = re.sub(r"[^a-z0-9]+", "-", device["model_identifier"].lower()).strip("-")
    relative = Path("runs") / timestamp.strftime("%Y/%m") / f"{model}-{run_id}.json"
    raw_path = root / relative
    raw_text = encoded(raw) + "\n"
    if raw_path.exists() and raw_path.read_text() != raw_text:
        raise ValueError(f"immutable run collision: {relative}")
    record = {"schema_version": 1, "kind": "run", "run_id": run_id,
              "raw": relative.as_posix(), "metadata": metadata,
              "comparison_key": hashlib.sha256(encoded(comparison).encode()).hexdigest(),
              **comparison, "statistics": summarize(report, metadata["valid"])}
    yearly = root / "statistics" / f"{timestamp.year}.jsonl"
    existing = {}
    if yearly.exists():
        for line in yearly.read_text().splitlines():
            item = json.loads(line)
            existing[item["run_id"]] = item
    if run_id in existing and existing[run_id] != record:
        raise ValueError(f"statistics collision: {run_id}")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_text)
    if run_id not in existing:
        yearly.parent.mkdir(parents=True, exist_ok=True)
        with yearly.open("a") as stream:
            stream.write(encoded(record) + "\n")
    return relative, yearly.relative_to(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    args = parser.parse_args()
    archive(args.archive_root, json.loads(args.report.read_text()),
            json.loads(args.metadata.read_text()))


if __name__ == "__main__":
    main()
