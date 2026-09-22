import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_gate", ROOT / "scripts/verify.py")
assert SPEC and SPEC.loader
verify_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_gate)


class VerifyGateTests(unittest.TestCase):
    def test_gate_has_lint_types_docs_and_one_covered_test_entrypoint(self):
        commands = verify_gate.commands("python", ["scripts/nrw_events/new.py"])
        self.assertIn(["python", "-m", "ruff", "check", "scripts", "tests"], commands)
        self.assertIn(["python", "-m", "mypy", "scripts/nrw_events/new.py"], commands)
        self.assertIn(["python", "scripts/generate_docs.py", "--check"], commands)
        self.assertEqual(commands.count(["bash", "scripts/test.sh"]), 1)
        self.assertNotIn(["bash", "scripts/test.sh"], verify_gate.commands("python", [], checks_only=True))

    def test_committed_and_untracked_modules_cannot_escape_changed_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git("init")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            folder = root / "scripts/nrw_events"
            folder.mkdir(parents=True)
            (folder / "old.py").write_text("value = 1\n")
            git("add", ".")
            git("commit", "-m", "baseline")
            (root / "typecheck-baseline.ref").write_text(git("rev-parse", "HEAD") + "\n")
            (folder / "old.py").write_text("value = 2\n")
            git("add", ".")
            git("commit", "-m", "change")
            (folder / "new.py").write_text("def untyped(x): return x\n")
            self.assertEqual(verify_gate.changed_modules(root), ["scripts/nrw_events/new.py", "scripts/nrw_events/old.py"])

    def test_explicit_gate_interpreter_wins_over_a_different_repository_venv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            script = root / "scripts/test.sh"
            script.write_text((ROOT / "scripts/test.sh").read_text())
            other = root / ".venv/bin/python"
            other.parent.mkdir(parents=True)
            other.write_text("#!/bin/sh\nexit 91\n")
            other.chmod(0o755)
            selected = root / "selected python"
            selected.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TRACE"\n')
            selected.chmod(0o755)
            trace = root / "trace"
            result = subprocess.run(
                ["bash", str(script), "tests.example"],
                env={**os.environ, "NRW_EVENTS_PYTHON": str(selected),
                     "NRW_EVENTS_COVERAGE": "0", "TRACE": str(trace)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(trace.read_text().splitlines(), ["-m", "unittest", "-v", "tests.example"])

    def test_canonical_gate_passes_its_own_python_to_the_shell_suite(self):
        versions = dict(line.split("==") for line in (ROOT / "requirements-dev.lock").read_text().splitlines()
                        if line.strip() and not line.startswith("#"))
        with patch.object(verify_gate.sys, "argv", ["verify.py"]), \
             patch.object(verify_gate, "version", side_effect=versions.__getitem__), \
             patch.object(verify_gate, "changed_modules", return_value=[]), \
             patch.object(verify_gate.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(verify_gate.main(), 0)
        shell = next(call for call in run.call_args_list if call.args[0] == ["bash", "scripts/test.sh"])
        self.assertEqual(shell.kwargs["env"]["NRW_EVENTS_PYTHON"], verify_gate.sys.executable)
        self.assertEqual(shell.kwargs["env"]["NRW_EVENTS_COVERAGE"], "1")
