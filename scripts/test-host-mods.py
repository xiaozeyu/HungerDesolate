import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("host_mods", PROJECT / "remote-scripts/probes/host-mods.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "33333333-3333-4333-8333-333333333333"
INSTANCE = "22222222-2222-4222-8222-222222222222"
REQUEST = "44444444-4444-4444-8444-444444444444"
DIGEST = "a" * 64


class HostModsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="host-mods-")
        self.root = Path(self.temp.name) / "hub"
        self.root.mkdir(mode=0o700)
        (self.root / "instances" / INSTANCE).mkdir(parents=True, mode=0o700)
        (self.root / "mods" / "prepared" / "123" / DIGEST / "content").mkdir(parents=True, mode=0o700)
        self.artifact = self.root / "mods" / "prepared" / "123" / DIGEST
        (self.artifact / "content" / "modinfo.lua").write_text("name='x'\n")
        self.write_json(self.artifact / "manifest.json", {
            "schemaVersion": 1, "workshopId": "123", "contentDigest": DIGEST,
            "sizeBytes": 9, "fileCount": 1, "preparedAt": "2026-01-01T00:00:00Z",
        })
        self.write_json(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write_json(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "instances": [
            {"instanceId": INSTANCE, "lifecycle": "ready", "relativePath": "instances/" + INSTANCE},
        ]})
        self.write_json(self.root / "instances" / INSTANCE / "manifest.json", {
            "schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE, "displayName": "生存一服",
        })
        self.write_json(self.root / "instances" / INSTANCE / "desired" / "mods.json", {
            "schemaVersion": 1, "instanceId": INSTANCE, "revision": 1,
            "mods": [{"workshopId": "123", "enabled": True, "configurationOptions": {}, "artifactDigest": DIGEST}],
        })
        self.patches = [patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "MODS", self.root / "mods"),
            patch.object(MODULE, "PREPARED", self.root / "mods" / "prepared"),
            patch.object(MODULE.os, "chown", return_value=None)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def request(self, action):
        return {"schemaVersion": 1, "requestId": REQUEST, "serverId": SERVER, "hostId": HOST,
                "expectedRecordVersion": 1, "action": action}

    def test_lists_artifact_with_instance_usage(self):
        data = MODULE.list_items(self.request("list"))
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["workshopId"], "123")
        self.assertEqual(data["items"][0]["usedBy"], [{"instanceId": INSTANCE, "displayName": "生存一服"}])

    def test_refuses_to_delete_used_artifact(self):
        request = self.request("delete")
        request.update({"workshopId": "123", "contentDigest": DIGEST})
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.delete_item(request)
        self.assertEqual(failure.exception.code, "HOST_MOD_IN_USE")

    def add_artifact(self, digest, prepared_at="2025-01-01T00:00:00Z"):
        artifact = self.root / "mods" / "prepared" / "123" / digest
        (artifact / "content").mkdir(parents=True, mode=0o700)
        (artifact / "content" / "modinfo.lua").write_text("name='x'\n")
        self.write_json(artifact / "manifest.json", {
            "schemaVersion": 1, "workshopId": "123", "contentDigest": digest,
            "sizeBytes": 9, "fileCount": 1, "preparedAt": prepared_at,
        })
        return artifact

    def test_cleanup_skips_used_and_backup_versions(self):
        old = "b" * 64
        self.add_artifact(old)
        self.write_json(self.root / "backups" / INSTANCE / "55555555-5555-4555-8555-555555555555" / "metadata.json",
                        {"schemaVersion": 1, "mods": [{"workshopId": "123", "artifactDigest": old}]})
        result = MODULE.cleanup(self.request("cleanup"))
        self.assertEqual(result["deleted"], [])

    def test_cleanup_deletes_older_unreferenced_version(self):
        old = "b" * 64
        artifact = self.add_artifact(old)
        result = MODULE.cleanup(self.request("cleanup"))
        self.assertEqual([item["contentDigest"] for item in result["deleted"]], [old])
        self.assertFalse(artifact.exists())

    def test_cleanup_keeps_versions_produced_by_the_current_update(self):
        old = "b" * 64
        artifact = self.add_artifact(old, prepared_at="2025-01-01T00:00:00Z")
        request = self.request("cleanup")
        request["keep"] = [{"workshopId": "123", "contentDigest": old}]
        result = MODULE.cleanup(request)
        self.assertEqual(result["deleted"], [])
        self.assertTrue(artifact.exists())

    def test_deletes_unused_artifact(self):
        self.write_json(self.root / "instances" / INSTANCE / "desired" / "mods.json", {
            "schemaVersion": 1, "instanceId": INSTANCE, "revision": 1, "mods": [],
        })
        request = self.request("delete")
        request.update({"workshopId": "123", "contentDigest": DIGEST})
        MODULE.delete_item(request)
        self.assertFalse(self.artifact.exists())



if __name__ == "__main__":
    unittest.main()
