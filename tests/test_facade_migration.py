"""Ratchet legacy imports; new code must expose its owning dependencies."""
import ast
import json
import logging
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from nrw_events.config import RuntimeConfig
from nrw_events.run_state import configure_context, reset_runtime
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.sources.bonn_policy import _active_reviewed_map

PACKAGE = Path(__file__).resolve().parents[1] / "scripts/nrw_events"
BASELINE = Path(__file__).with_name("data") / "legacy-facade-imports.json"


def facade_dependencies(source: str, module: str) -> set[str]:
    dependencies = set()
    package = module.split(".")[:-1]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            dependencies.update(
                alias.name for alias in node.names
                if alias.name in {"nrw_events.common", "nrw_events.core"}
            )
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            for alias in node.names:
                name = f"{base}.{alias.name}"
                if base in {"nrw_events.common", "nrw_events.core"} or name in {"nrw_events.common", "nrw_events.core"}:
                    dependencies.add(name)
    return dependencies


def inventory() -> dict[str, list[str]]:
    result = {}
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE)
        module = "nrw_events." + ".".join(relative.with_suffix("").parts)
        dependencies = facade_dependencies(path.read_text(), module)
        if dependencies:
            result[relative.as_posix()] = sorted(dependencies)
    return result


class FacadeMigrationTests(unittest.TestCase):
    def test_facade_imports_cannot_expand_and_removed_imports_leave_baseline(self):
        self.assertEqual(inventory(), json.loads(BASELINE.read_text()),
                         "Migrate dependencies to owning modules; remove retired baseline entries.")

    def test_detector_covers_relative_absolute_and_symbol_imports(self):
        self.assertEqual(facade_dependencies(
            "from .. import common\nfrom ..core import parse_date\n"
            "import nrw_events.core\nfrom nrw_events import common",
            "nrw_events.sources.adapter",
        ), {"nrw_events.common", "nrw_events.core.parse_date", "nrw_events.core"})
        self.assertEqual(facade_dependencies(
            "from ..dates import parse_date", "nrw_events.sources.adapter"), set())

    def test_parallel_bonn_policy_runs_keep_their_expiry_windows(self):
        barrier = threading.Barrier(2)
        group = "bonn_press_occurrence_corrections"
        def run(year):
            start = datetime(year, 9, 1)
            context = RunContext(RuntimeConfig(), EventWindow(start, start),
                                 str(year), logging.getLogger("isolation-test"))
            expected = _active_reviewed_map(group, context)
            token = configure_context(context)
            try:
                barrier.wait(timeout=5)
                for _ in range(100):
                    self.assertEqual(_active_reviewed_map(group), expected)
                return expected
            finally:
                reset_runtime(token)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, 2026)
            second = pool.submit(run, 2099)
            self.assertTrue(first.result(timeout=10))
            self.assertEqual(second.result(timeout=10), {})
