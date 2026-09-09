#!/usr/bin/env python3
"""Run explicitly scoped Docker acceptance and retain reproducible evidence."""

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]


def command(args, log):
    # Arguments contain no credentials. Compose reads the private local .env itself.
    with subprocess.Popen(
        [str(PACKAGE / "scripts/compose.sh"), *args],
        cwd=PACKAGE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        return process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["foundation"])
    parser.add_argument("--build", action="store_true", help="Rebuild current source images first")
    args = parser.parse_args()
    if not (PACKAGE / ".env").is_file():
        parser.error("Run knowledge-docker/scripts/init.py first")
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = PACKAGE / "artifacts" / (args.stage + "-" + stamp)
    directory.mkdir(parents=True, mode=0o700)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PACKAGE, text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=PACKAGE, text=True
    ).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=PACKAGE, text=True
    ).strip())
    report = {
        "stage": args.stage,
        "started_at": stamp,
        "branch": branch,
        "head": head,
        "dirty_worktree": dirty,
        "build_requested": args.build,
        "coverage": "Foundation only; not full V2 acceptance or real model/bot acceptance",
        "steps": [],
        "passed": False,
    }
    steps = []
    if args.build:
        steps.append(("build", ["build", "gateway", "iam", "auth", "knowledge", "regression", "go-regression"]))
    steps.extend([
        ("start", ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "keycloak", "valkey", "gateway", "iam", "auth", "knowledge"]),
        ("go", ["run", "--rm", "go-regression"]),
        ("vet", ["run", "--rm", "go-regression", "go", "vet", "./..."]),
        ("python", ["run", "--rm", "regression", "python", "-m", "pytest", "services/python/tests/common", "services/python/tests/knowledge", "services/python/tests/ingest", "-q"]),
        ("http", ["run", "--rm", "regression"]),
    ])
    try:
        for name, arguments in steps:
            print(f"Docker regression: {name}", flush=True)
            with (directory / (name + ".log")).open("w") as log:
                status = command(arguments, log)
            report["steps"].append({"name": name, "exit_code": status})
            if status:
                return status
        report["passed"] = True
        return 0
    finally:
        report["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Regression evidence: {directory}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
