#!/usr/bin/env python3
"""Archive hardware reports and publish them through a topic-branch pull request."""

import argparse
import datetime as dt
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from archive import archive, encoded, validate_identity


def command(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120)
    if result.returncode:
        logging.error("Command failed: %s\n%s", args, result.stderr.strip())
    result.check_returncode()
    return result.stdout.strip()


def merge_when_ready(url, head, checkout):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = json.loads(command(["gh", "pr", "view", url, "--json",
                                    "mergeable,statusCheckRollup,headRefOid"], checkout))
        if state["headRefOid"] != head:
            raise RuntimeError(f"publication head changed: {url}")
        if state["mergeable"] == "CONFLICTING":
            raise RuntimeError(f"publication conflicts with the base branch: {url}")
        pending = False
        for check in state["statusCheckRollup"]:
            status = check.get("conclusion") or check.get("state") or check["status"]
            if status in ("PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"):
                pending = True
            elif status not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                raise RuntimeError(f"publication check did not pass: {check}")
        if state["mergeable"] == "MERGEABLE" and not pending:
            command(["gh", "pr", "merge", url, "--merge", "--match-head-commit", head], checkout)
            return
        time.sleep(2)
    raise TimeoutError(f"publication did not become ready within 60 seconds: {url}")


class RunArchive:
    """A durable upload journal, shared by runners and standalone experiments."""

    def __init__(self, directory, metadata):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        validate_identity(metadata)
        self.metadata = metadata
        identity = self.directory / "session.json"
        if identity.exists() and json.loads(identity.read_text()) != metadata:
            raise ValueError("output directory belongs to a different build or device; use a new directory")
        identity.write_text(encoded(metadata) + "\n")

    def record(self, report_path, configuration, valid=True, **observations):
        report_path = Path(report_path).resolve()
        metadata = dict(self.metadata,
                        configuration={**self.metadata.get("configuration", {}), **configuration}, valid=valid,
                        observations=observations,
                        recorded_at=dt.datetime.fromtimestamp(
                            report_path.stat().st_mtime, dt.timezone.utc).isoformat())
        # The pulled report's timestamp survives an upload retry. The journal is
        # written before any network activity and is the retry input.
        report = json.loads(report_path.read_text())
        if "unsupported" in report:
            # The engine declined the scene and rendered nothing: there is no
            # measurement to archive.
            logging.warning("%s cannot render %s (%s); nothing archived",
                            report["engine"], report["scene"], report["unsupported"])
            return False
        archive(self.directory, report, metadata)
        return True

    def publish(self, repository):
        publish(self.directory, repository)


def publish(directory, repository):
    directory = Path(directory).resolve()
    raw_paths = sorted(directory.glob("runs/*/*/*.json"))
    if not raw_paths:
        return
    repository = Path(repository).resolve()
    command(["git", "fetch", "origin", "main"], repository)
    branch = "data/hardware-" + uuid.uuid4().hex
    checkout = Path(tempfile.mkdtemp(prefix="cherenkov-archive-")) / "checkout"
    command(["git", "worktree", "add", "-b", branch, str(checkout), "origin/main"], repository)
    succeeded = False
    try:
        for path in raw_paths:
            raw = json.loads(path.read_text())
            archive(checkout, raw["report"], raw["metadata"])
        command(["git", "add", "runs", "statistics"], checkout)
        if not command(["git", "diff", "--cached", "--name-only"], checkout):
            succeeded = True
            return
        command(["git", "commit", "-S", "-m", "data: archive hardware benchmark measurements"], checkout)
        command(["git", "push", "-u", "origin", branch], checkout)
        body = checkout.parent / "pr-body.md"
        body.write_text("Archive independent hardware runs with device identity, source revisions, "
                        "measurement conditions and per-run statistics. Raw reports are retained "
                        "under their measurement year and month; yearly statistics link each report.\n\n"
                        "Statistics were recomputed from the individual frame samples during import.\n")
        url = command(["gh", "pr", "create", "--base", "main", "--head", branch,
                       "--title", "data: archive hardware benchmark measurements",
                       "--body-file", str(body)], checkout)
        head = command(["git", "rev-parse", "HEAD"], checkout)
        merge_when_ready(url, head, checkout)
        logging.info("Published hardware archive: %s", url)
        succeeded = True
    except Exception:
        logging.exception("Upload failed; reports remain in %s; publication checkout: %s",
                          directory, checkout)
        raise
    finally:
        # Reports remain in the journal even when GitHub is unavailable.
        if succeeded:
            command(["git", "worktree", "remove", str(checkout)], repository)
            command(["git", "branch", "-D", branch], repository)


def runner_arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--device", required=True, help="adb serial or Apple device identifier")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path,
                        help="JSON with device {model, model_identifier, os, gpu} and source {revision}")
    parser.add_argument("--repository", required=True, type=Path,
                        help="local cherenkov-benchmarks repository; uploads use a separate worktree")
    return parser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--manifest", type=Path,
                        help="JSON array of {report: path, metadata: object}; omit to retry upload")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.manifest:
        for item in json.loads(args.manifest.read_text()):
            path = args.manifest.parent / item["report"]
            archive(args.journal, json.loads(path.read_text()), item["metadata"])
    publish(args.journal, args.repository)


if __name__ == "__main__":
    main()
