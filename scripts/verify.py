#!/usr/bin/env python3
"""Canonical offline gate: lint, types, docs and covered regression tests.

The immutable initial revision inventories legacy typing debt. Every production
module changed since that revision is additionally checked, including untracked
files. Committing a change cannot remove it from this scope.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changed_modules(root: Path = ROOT) -> list[str]:
    base = (root / "typecheck-baseline.ref").read_text().strip()
    # Resolve the stored commit, never interpolate a caller-provided shell expression.
    subprocess.run(["git", "cat-file", "-e", base + "^{commit}"], cwd=root, check=True,
                   capture_output=True)
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "-z", base, "--", "scripts/nrw_events"],
        cwd=root,
    ).decode().split("\0")
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "scripts/nrw_events"],
        cwd=root,
    ).decode().split("\0")
    return sorted({name for name in changed + untracked
                   if name.endswith(".py") and (root / name).is_file()})


def commands(python: str, changed: list[str], *, checks_only: bool = False) -> list[list[str]]:
    result = [
        [python, "-m", "ruff", "check", "scripts", "tests"],
        [python, "-m", "mypy"],
    ]
    if changed:
        result.append([python, "-m", "mypy", *changed])
    result.extend([
        [python, "scripts/generate_docs.py", "--check"],
        [python, "scripts/agent_tools.py", "check"],
    ])
    if not checks_only:
        result.append(["bash", "scripts/test.sh"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="store_true")
    parser.add_argument("--checks-only", action="store_true", help="Focused lint/type/docs check; not a release gate")
    args = parser.parse_args()
    if args.agent:
        from agent_test_runner import run
        folder = ROOT / ".cache/agent-verification" / (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
        command = [sys.executable, str(Path(__file__).resolve())]
        if args.checks_only:
            command.append("--checks-only")
        return run(command, folder)
    for line in (ROOT / "requirements-dev.lock").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, expected = line.split("==")
        try:
            actual = version(name)
        except PackageNotFoundError:
            actual = "missing"
        if actual != expected:
            print(f"{name}: expected {expected}, found {actual}; install requirements-dev.lock", file=sys.stderr)
            return 2
    env = {**os.environ, "NRW_EVENTS_COVERAGE": "1", "NRW_EVENTS_PYTHON": sys.executable}
    for command in commands(sys.executable, changed_modules(), checks_only=args.checks_only):
        print("[verify] " + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
