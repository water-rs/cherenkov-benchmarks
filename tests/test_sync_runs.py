import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from sync_runs import RunArchive, command, publish


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

    def test_network_failure_preserves_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal, _ = self.journal(root)
            with patch("sync_runs.command", side_effect=subprocess.TimeoutExpired("git", 120)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    publish(journal.directory, root)
            self.assertEqual(len(list(journal.directory.glob("runs/*/*/*.json"))), 1)
