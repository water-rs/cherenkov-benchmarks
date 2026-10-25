import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("archive", Path(__file__).parents[1] / "scripts/archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {"recorded_at": "2026-01-01T01:00:00+00:00",
                         "device": {"model": "Pixel 9 Pro", "model_identifier": "caiman", "os": "Android 16", "gpu": "Mali-G715"},
                         "source": {"revision": "a" * 40}, "valid": True,
                         "configuration": {"mode": "fixed-clocks"}}
        self.report = {"engine": "cherenkov", "scene": "list", "width": 10, "height": 20,
                       "samples": [{"encode_seconds": .001, "submit_seconds": .009, "gpu_seconds": .002},
                                   {"encode_seconds": .009, "submit_seconds": .001, "gpu_seconds": .012}]}

    def test_frame_statistics_use_paired_samples(self):
        stats = archive.summarize(self.report, True)
        for metric, expected in {"cpu": {"p50": 10, "p99": 10},
                                 "frame": {"p50": 10, "p99": 12}}.items():
            for quantile, value in expected.items():
                self.assertAlmostEqual(stats["timing_ms"][metric][quantile], value)

    def test_archive_is_idempotent_and_uses_eastern_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, yearly = archive.archive(root, self.report, self.metadata)
            first = (root / yearly).read_bytes()
            archive.archive(root, self.report, self.metadata)
            self.assertEqual((root / yearly).read_bytes(), first)
            self.assertEqual(raw.parts[:3], ("runs", "2025", "12"))
            self.assertEqual(yearly, Path("statistics/2025.jsonl"))
            self.assertEqual(json.loads((root / raw).read_text())["report"], self.report)

    def test_device_models_are_distinct_comparison_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive.archive(root, self.report, self.metadata)
            other = copy.deepcopy(self.metadata)
            other["device"] = {"model": "iPad Pro M4", "model_identifier": "iPad16,5", "os": "iPadOS 26.5", "gpu": "Apple M4"}
            _, yearly = archive.archive(root, self.report, other)
            rows = [json.loads(line) for line in (root / yearly).read_text().splitlines()]
            self.assertNotEqual(rows[0]["comparison_key"], rows[1]["comparison_key"])

    def test_model_identifier_and_os_distinguish_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = copy.deepcopy(self.metadata)
            metadata["device"] = {"model": "iPad Pro", "model_identifier": "iPad16,5",
                                  "os": "iPadOS 26.5", "gpu": "Apple M4"}
            first_raw, yearly = archive.archive(root, self.report, metadata)
            other_model = copy.deepcopy(metadata)
            other_model["device"]["model_identifier"] = "iPad16,6"
            other_raw, _ = archive.archive(root, self.report, other_model)
            other_os = copy.deepcopy(metadata)
            other_os["device"]["os"] = "iPadOS 26.6"
            archive.archive(root, self.report, other_os)
            rows = [json.loads(line) for line in (root / yearly).read_text().splitlines()]
            self.assertEqual(len({row["comparison_key"] for row in rows}), 3)
            self.assertTrue(first_raw.name.startswith("ipad16-5-"))
            self.assertTrue(other_raw.name.startswith("ipad16-6-"))

    def test_invalid_runs_are_retained_without_performance_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = dict(self.metadata, valid=False)
            report = dict(self.report, samples=[])
            _, yearly = archive.archive(Path(directory), report, metadata)
            self.assertIsNone(json.loads((Path(directory) / yearly).read_text())["statistics"])

    def test_partial_gpu_timestamps_are_not_reported_as_cpu_only(self):
        self.report["samples"][1]["gpu_seconds"] = None
        with self.assertRaises(ValueError):
            archive.summarize(self.report, True)


if __name__ == "__main__":
    unittest.main()
