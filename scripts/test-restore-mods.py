import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("restore_task", PROJECT / "remote-scripts/actions/restore-task.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
TASK = "44444444-4444-4444-8444-444444444444"
DIGEST = "a" * 64
RELEASE = "55555555-5555-4555-8555-555555555555"


class RestoreModsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="restore-mods-")
        self.root = Path(self.temp.name) / "hub"
        (self.root / "mods" / "prepared" / "123" / DIGEST / "content").mkdir(parents=True)
        self.artifact = self.root / "mods" / "prepared" / "123" / DIGEST
        (self.artifact / "content" / "modinfo.lua").write_text("version = '1.0'\nconfiguration_options = {}\n")
        self.write(self.artifact / "manifest.json", {"schemaVersion": 1, "workshopId": "123",
            "contentDigest": DIGEST, "preparedAt": "2026-01-01T00:00:00Z", "sizeBytes": 1, "fileCount": 1})
        self.staging = self.root / "staging"
        (self.staging / "data" / "Master").mkdir(parents=True)
        (self.staging / "config" / "Cluster_1" / "Master").mkdir(parents=True)
        self.write(self.staging / "manifest.json", {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
            "cavesEnabled": False, "configRevision": 1})
        self.write(self.staging / "active-release.json", {"schemaVersion": 1, "dstReleaseId": RELEASE,
            "modsReleaseId": None})
        self.desired = {"schemaVersion": 1, "instanceId": INSTANCE, "revision": 4,
            "mods": [{"workshopId": "123", "enabled": True, "configurationOptions": {}, "artifactDigest": "b" * 64}]}
        self.original_active = {"schemaVersion": 1, "dstReleaseId": RELEASE, "modsReleaseId": None}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_missing_host_mod_warns_and_restores_without_release(self):
        apply_ns = {"__name__": "restore_apply"}
        exec(compile((PROJECT / "remote-scripts/actions/mods-apply.py").read_text(), "<mods-apply>", "exec"), apply_ns)
        download_ns = {"__name__": "restore_download"}
        exec(compile((PROJECT / "remote-scripts/actions/mods-download.py").read_text(), "<mods-download>", "exec"), download_ns)
        apply_ns["ROOT"] = self.root
        download_ns["ROOT"] = self.root
        self.desired["mods"][0]["workshopId"] = "999"
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "safe_dir", lambda path, create=False, runtime=False: None), patch("os.chown", return_value=None):
            release_id, warnings = MODULE.rebuild_restore_mods(
                self.staging, INSTANCE, self.desired, 4, TASK, ["master"], self.original_active, apply_ns, download_ns)
        self.assertIsNone(release_id)
        self.assertEqual(warnings, ["MOD_MISSING:999"])
        self.assertFalse((self.staging / "mods" / "releases").exists())
        self.assertIsNone(json.loads((self.staging / "active-release.json").read_text()).get("modsReleaseId"))

    def test_rebuild_uses_latest_host_artifact(self):
        apply_ns = {"__name__": "restore_apply"}
        exec(compile((PROJECT / "remote-scripts/actions/mods-apply.py").read_text(), "<mods-apply>", "exec"), apply_ns)
        download_ns = {"__name__": "restore_download"}
        exec(compile((PROJECT / "remote-scripts/actions/mods-download.py").read_text(), "<mods-download>", "exec"), download_ns)
        apply_ns["ROOT"] = self.root
        apply_ns["safe_dir"] = lambda path, create=False, runtime=False: None
        download_ns["ROOT"] = self.root
        _, _, actual = download_ns["content_files"](self.artifact / "content")
        if actual != DIGEST:
            renamed = self.artifact.parent / actual
            self.artifact.rename(renamed)
            self.artifact = renamed
            self.write(self.artifact / "manifest.json", {"schemaVersion": 1, "workshopId": "123",
                "contentDigest": actual, "preparedAt": "2026-01-01T00:00:00Z", "sizeBytes": 1, "fileCount": 1})
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "safe_dir", lambda path, create=False, runtime=False: None), patch("os.chown", return_value=None):
            release_id, warnings = MODULE.rebuild_restore_mods(
                self.staging, INSTANCE, self.desired, 4, TASK, ["master"], self.original_active, apply_ns, download_ns)
        self.assertEqual(warnings, ["MOD_VERSION_LATEST:123"])
        self.assertIsNotNone(release_id)
        release = self.staging / "mods" / "releases" / release_id
        self.assertTrue((release / "manifest.json").is_file())
        self.assertTrue((release / "Master" / "steamapps" / "workshop" / "content" / "322330" / "123" / "modinfo.lua").is_file())
        self.assertEqual(self.desired["mods"][0]["artifactDigest"], self.artifact.name)
        active = json.loads((self.staging / "active-release.json").read_text())
        self.assertEqual(active["modsReleaseId"], release_id)


if __name__ == "__main__":
    unittest.main()
