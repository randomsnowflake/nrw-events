import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_tools import inspect_snapshot, source_context, validate_map


class AgentToolsTests(unittest.TestCase):
    def test_task_paths_exist(self):
        self.assertEqual(validate_map(), [])

    def test_python_and_declarative_sources_resolve_without_fetching(self):
        bonn = source_context('bonn-de-events')
        self.assertIn('scripts/nrw_events/sources/bonn.py', bonn['files'])
        self.assertTrue(bonn['test_references'])
        for record in bonn['test_references']:
            self.assertTrue((ROOT / record['path']).is_file())
        self.assertIn('scripts/nrw_events/ical.py', source_context('wachtberg')['files'])
        with self.assertRaises(ValueError):
            source_context('missing-source')

    def test_exact_record_alias_source_url_and_bounded_full_output(self):
        event = {'event_id': 'event-abc', 'title': 'A', 'previous_event_ids': ['old-id'], 'link': 'https://example.test/detail', 'description': 'large text'}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.json'
            path.write_text(json.dumps({'events': [event, event], 'run_id': 'recorded'}))
            for query in ('old-id', 'https://www.veranstaltungen-bonn.de/veranstaltungen/event-abc/', event['link']):
                result = inspect_snapshot(query, path, limit=1)
                self.assertEqual(result['match_count'], 2)
                self.assertEqual(result['omitted'], 1)
                self.assertNotIn('description', result['matches'][0]['record'])
            self.assertEqual(inspect_snapshot('event-abc', path, full=True)['matches'][0]['record']['description'], 'large text')
            self.assertEqual(inspect_snapshot('not-found', path)['match_count'], 0)
            path.write_text('{}')
            with self.assertRaises(ValueError):
                inspect_snapshot('event-abc', path)

    def test_logged_runner_preserves_failure_exit_and_complete_log(self):
        spec = importlib.util.spec_from_file_location('agent_test_runner', ROOT / 'scripts/agent_test_runner.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            directory = Path(folder)
            code = module.run([sys.executable, '-c', "print('first failure evidence'); print('x'*20000); raise SystemExit(7)"], directory)
            self.assertEqual(code, 7)
            summary = json.loads((directory / 'summary.json').read_text())
            self.assertEqual(summary['exit_code'], 7)
            self.assertGreater(summary['log_bytes'], 20000)
            self.assertIn('first failure evidence', (directory / 'tests.log').read_text())

    def test_cli_rejects_unknown_source(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/agent_tools.py'), 'context', 'source', 'does-not-exist'], check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unknown source', result.stderr)
