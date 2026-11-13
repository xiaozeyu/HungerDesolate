import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("restore_task", PROJECT / "remote-scripts/actions/restore-task.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

INSTANCE = "22222222-2222-4222-8222-222222222222"
DIGEST = "a" * 64


class RestoreModWarningsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="restore-mods-")
        self.instance = Path(self.temp.name) / "instance"
        (self.instance / "desired").mkdir(parents=True, mode=0o700)
        (self.instance / "desired").chmod(0o700)
        self.write(self.instance / "desired" / "mods.json", {
            "schemaVersion": 1, "instanceId": INSTANCE, "revision": 1,
            "mods": [{"workshopId": "123", "enabled": True, "artifactDigest": DIGEST}],
        })

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_enabled_mod_missing_from_current_instance_warns(self):
        metadata = {"mods": [{"workshopId": "999", "enabled": True, "artifactDigest": DIGEST}]}
        self.assertEqual(MODULE.mod_warnings(self.instance, metadata), ["MOD_MISSING:999"])

    def test_changed_artifact_digest_warns_without_blocking(self):
        metadata = {"mods": [{"workshopId": "123", "enabled": True, "artifactDigest": "b" * 64}]}
        self.assertEqual(MODULE.mod_warnings(self.instance, metadata), ["MOD_VERSION_CHANGED:123"])

    def test_matching_artifact_and_disabled_mods_do_not_warn(self):
        metadata = {"mods": [
            {"workshopId": "123", "enabled": True, "artifactDigest": DIGEST},
            {"workshopId": "999", "enabled": False, "artifactDigest": DIGEST},
        ]}
        self.assertEqual(MODULE.mod_warnings(self.instance, metadata), [])


if __name__ == "__main__":
    unittest.main()
