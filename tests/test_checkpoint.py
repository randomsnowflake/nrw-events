"""Import recovery must never silently accept changed or partial artifacts."""
import json
import tempfile
import unittest
from pathlib import Path

from nrw_events.checkpoint import capture, restore, validate


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = {}
        for name, value in {
            "events": [{"event_id": "retained"}],
            "metadata": {"run_status": "degraded", "run_id": "one"},
            "highlights": {}, "series": {"series": ["preserved"]},
        }.items():
            path = self.root / f"input-{name}.json"
            path.write_text(json.dumps(value))
            self.files[name] = path
        self.checkpoint = self.root / "checkpoint"

    def test_roundtrip_preserves_all_outputs(self):
        capture(self.checkpoint, "identity", self.files)
        targets = {name: self.root / "restored" / path.name for name, path in self.files.items()}
        restore(self.checkpoint, "identity", targets)
        for name, target in targets.items():
            self.assertEqual(target.read_bytes(), self.files[name].read_bytes())

    def test_changed_identity_refuses_restore_before_writes(self):
        capture(self.checkpoint, "identity", self.files)
        with self.assertRaisesRegex(ValueError, "changed"):
            restore(self.checkpoint, "other", {"events": self.root / "must-not-exist"})
        self.assertFalse((self.root / "must-not-exist").exists())

    def test_corruption_and_incomplete_inventory_rejected(self):
        capture(self.checkpoint, "identity", self.files)
        (self.checkpoint / "series.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate(self.checkpoint, "identity")

    def test_failed_import_never_checkpointed(self):
        self.files["metadata"].write_text('{"run_status":"failed"}')
        with self.assertRaisesRegex(ValueError, "unsuccessful"):
            capture(self.checkpoint, "identity", self.files)
        self.assertFalse(self.checkpoint.exists())

    def test_expired_checkpoint_rejected(self):
        capture(self.checkpoint, "identity", self.files)
        path = self.checkpoint / "manifest.json"
        value = json.loads(path.read_text())
        value["created_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "expired"):
            validate(self.checkpoint, "identity")
