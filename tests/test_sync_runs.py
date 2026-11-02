import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from sync_runs import RunArchive, command, publish, merge_when_ready
from run_pixel9_bench import record_cell, run_cell


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {"device": {"model": "Pixel 9 Pro", "model_identifier": "caiman",
                                    "os": "Android 16", "gpu": "Mali-G715"},
                         "source": {"revision": "a" * 40},
                         "configuration": {"clocks": "fixed"}}
        self.report = {"engine": "cherenkov", "scene": "map", "width": 10, "height": 20,
                       "samples": [{"encode_seconds": .001, "submit_seconds": .002,
                                    "gpu_seconds": .004}]}

    def journal(self, root):
        report = root / "report.json"
        report.write_text(json.dumps(self.report))
        journal = RunArchive(root / "journal", self.metadata)
        journal.record(report, {"frames": 1})
        return journal, report

    def test_resume_does_not_duplicate_or_relabel_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal, report = self.journal(root)
            RunArchive(journal.directory, self.metadata).record(report, {"frames": 1})
            rows = [json.loads(line) for path in journal.directory.glob("statistics/*.jsonl")
                    for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["configuration"]["clocks"], "fixed")
            with self.assertRaisesRegex(ValueError, "different build or device"):
                RunArchive(journal.directory, dict(self.metadata, source={"revision": "b" * 40}))

    def test_unsupported_report_is_not_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            report.write_text(json.dumps({"engine": "skia-vulkan", "scene": "effects",
                                          "unsupported": {"feature": "blend-space",
                                                          "value": "srgb-encoded"}}))
            journal = RunArchive(root / "journal", self.metadata)
            with self.assertLogs(level="WARNING"):
                self.assertFalse(record_cell(report, {"frames": 1}, journal))
            self.assertEqual(list(journal.directory.glob("runs/*/*/*.json")), [])

    def test_pixel_resume_preserves_unquiet_reports_as_invalid(self):
        for screen, thermal in (("awake", "none"), ("dozing", "moderate")):
            with self.subTest(screen=screen, thermal=thermal), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report = root / "gpu_r0_map_cherenkov.json"
                report.write_text(json.dumps(dict(
                    self.report, conditions={"screen_state": screen, "thermal_status": thermal})))
                journal = RunArchive(root / "journal", self.metadata)
                options = SimpleNamespace(device="test-device", output=root)
                with patch("run_pixel9_bench.run_adb_cmd") as adb:
                    with self.assertRaisesRegex(RuntimeError, "quiet device"):
                        run_cell("cherenkov", "map", 0, True, options, journal)
                    adb.assert_not_called()
                rows = [json.loads(line) for path in journal.directory.glob("statistics/*.jsonl")
                        for line in path.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertFalse(rows[0]["metadata"]["valid"])
                self.assertIsNone(rows[0]["statistics"])
                raw = json.loads((journal.directory / rows[0]["raw"]).read_text())
                self.assertEqual(raw["report"]["samples"], self.report["samples"])

    def test_pixel_sleeps_before_measurement_and_checks_the_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = RunArchive(root / "journal", self.metadata)
            options = SimpleNamespace(device="test-device", output=root)

            def pull(args, **kwargs):
                self.assertEqual(args[0:4], ["adb", "-s", "test-device", "pull"])
                Path(args[-1]).write_text(json.dumps(dict(
                    self.report, conditions={"screen_state": "dozing", "thermal_status": "none"})))

            with patch("run_pixel9_bench.subprocess.run", side_effect=pull):
                with patch("run_pixel9_bench.run_adb_cmd",
                           side_effect=["", "mWakefulness=Dozing", "", ""]) as adb:
                    run_cell("cherenkov", "map", 0, True, options, journal)
            calls = [call.args[1] for call in adb.call_args_list]
            self.assertEqual(calls[:2], ["input keyevent KEYCODE_SLEEP", "dumpsys power"])
            self.assertTrue(calls[2].startswith("/data/local/tmp/cherenkov-bench measure "))
            rows = [json.loads(line) for path in journal.directory.glob("statistics/*.jsonl")
                    for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["metadata"]["valid"])

    def test_pixel_does_not_measure_when_sleep_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            options = SimpleNamespace(device="test-device", output=Path(directory))
            with patch("run_pixel9_bench.run_adb_cmd",
                       side_effect=["", "mWakefulness=Awake"]) as adb:
                with self.assertRaisesRegex(RuntimeError, "did not enter sleep"):
                    run_cell("cherenkov", "map", 0, True, options, None)
            self.assertEqual(adb.call_count, 2)

    def test_publish_uses_topic_branch_and_retry_does_not_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal, _ = self.journal(root)
            repository, remote = root / "repo", root / "remote.git"
            command(["git", "init", "--bare", str(remote)], root)
            command(["git", "init", "-b", "main", str(repository)], root)
            command(["git", "config", "user.name", "Archive test"], repository)
            command(["git", "config", "user.email", "archive@example.invalid"], repository)
            command(["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture"], repository)
            command(["git", "remote", "add", "origin", str(remote)], repository)
            command(["git", "push", "origin", "main"], repository)
            publications = []

            def execute(args, cwd):
                if args[0] == "gh":
                    if args[1:3] == ["pr", "create"]:
                        publications.append(args)
                        return "https://example.invalid/pr/1"
                    if args[1:3] == ["pr", "view"]:
                        return json.dumps({"headRefOid": command(["git", "rev-parse", "HEAD"], cwd),
                                           "mergeable": "MERGEABLE", "statusCheckRollup": []})
                    if args[1:3] == ["pr", "merge"]:
                        # Emulate GitHub merging the topic into the local test remote.
                        head = command(["git", "rev-parse", "HEAD"], cwd)
                        command(["git", "update-ref", "refs/heads/main", head], remote)
                        return ""
                    self.fail(f"unexpected GitHub call: {args}")
                if args[:3] == ["git", "commit", "-S"]:
                    args = ["git", "-c", "commit.gpgsign=false", "commit", *args[3:]]
                return command(args, cwd)

            with patch("sync_runs.command", side_effect=execute):
                publish(journal.directory, repository)
                publish(journal.directory, repository)
            self.assertEqual(len(publications), 1)
            files = command(["git", "ls-tree", "-r", "--name-only", "main"], remote).splitlines()
            self.assertEqual(len(files), 2)
            self.assertEqual(command(["git", "status", "--porcelain"], repository), "")
            self.assertEqual(len(list(journal.directory.glob("runs/*/*/*.json"))), 1)

    def test_waits_for_github_readiness_without_auto_merge(self):
        states = [json.dumps({"headRefOid": "a", "mergeable": ready, "statusCheckRollup": []})
                  for ready in ("UNKNOWN", "MERGEABLE")]
        with patch("sync_runs.command", side_effect=[*states, ""]) as run:
            with patch("sync_runs.time.sleep") as sleep:
                merge_when_ready("pr-url", "a", Path("."))
        sleep.assert_called_once_with(2)
        self.assertEqual(run.call_args.args[0],
                         ["gh", "pr", "merge", "pr-url", "--merge", "--match-head-commit", "a"])

    def test_network_failure_preserves_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal, _ = self.journal(root)
            with patch("sync_runs.command", side_effect=subprocess.TimeoutExpired("git", 120)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    publish(journal.directory, root)
            self.assertEqual(len(list(journal.directory.glob("runs/*/*/*.json"))), 1)
