"""Test guarded cleanup of empty failed instance registrations."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cleanup_orphan_instance", PROJECT / "remote-scripts/actions/cleanup-orphan-instance.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
TASK = "33333333-3333-4333-8333-333333333333"


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cleanup-orphan-instance-")
        self.root = Path(self.temp.name) / "famine-hub"
        (self.root / "locks").mkdir(parents=True)
        (self.root / "instances" / INSTANCE).mkdir(parents=True)
        self.entry = {"instanceId": INSTANCE, "lifecycle": "createFailed",
                      "relativePath": "instances/" + INSTANCE, "displayName": "残留服",
                      "pendingTaskId": TASK}
        self.registry = {"schemaVersion": 1, "hostId": HOST, "registryRevision": 3,
                         "instances": [self.entry],
                         "portReservations": [{"hostId": HOST, "instanceId": INSTANCE, "hostPort": 11000}],
                         "operations": []}
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (self.root / "registry.json").write_text(json.dumps(self.registry))
        (self.root / "host.json").chmod(0o600)
        (self.root / "registry.json").chmod(0o600)
        (self.root / "tasks").mkdir()
        task_dir = self.root / "tasks" / TASK
        task_dir.mkdir()
        (task_dir / "admission.json").write_text(json.dumps({"instanceId": INSTANCE}))
        (task_dir / "status.json").write_text(json.dumps({"status": "failed"}))
        (task_dir / "admission.json").chmod(0o600)
        (task_dir / "status.json").chmod(0o600)
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned_path(path):
            values = list(original_lstat(path)); values[4] = 0
            return os.stat_result(values)
        def root_owned_fd(fd):
            values = list(original_fstat(fd)); values[4] = 0
            return os.stat_result(values)
        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "REGISTRY", self.root / "registry.json"),
            patch.object(MODULE, "LOCK", self.root / "locks" / "registry.lock"),
            patch.object(MODULE, "INSTANCES", self.root / "instances"),
            patch.object(MODULE, "TASKS", self.root / "tasks"),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(MODULE, "docker_ids", return_value=[]),
            patch.object(MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=1)),
            patch.object(Path, "lstat", root_owned_path),
            patch.object(MODULE.os, "fstat", root_owned_fd),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self):
        return {"hostId": HOST, "instanceId": INSTANCE, "expectedRegistryRevision": 3, "typedName": "残留服"}

    def test_removes_empty_instance_directory_and_releases_its_ports(self):
        result = MODULE.remove(self.request())
        registry = json.loads((self.root / "registry.json").read_text())
        self.assertTrue(result["removed"])
        self.assertFalse(result["retired"])
        self.assertEqual(result["releasedReservations"], 1)
        self.assertEqual(registry["instances"], [])
        self.assertEqual(registry["portReservations"], [])
        self.assertFalse((self.root / "instances" / INSTANCE).exists())
        self.assertFalse((self.root / "retired" / INSTANCE).exists())

    def test_preserves_manifest_and_world_data_in_retired_directory(self):
        manifest = self.root / "instances" / INSTANCE / "manifest.json"
        manifest.write_text(json.dumps({"hostId": HOST, "instanceId": INSTANCE}))
        manifest.chmod(0o600)
        result = MODULE.remove(self.request())
        self.assertTrue(result["retired"])
        self.assertTrue((self.root / "retired" / INSTANCE / "manifest.json").exists())

    def test_refuses_docker_resources_and_live_tasks(self):
        with patch.object(MODULE, "docker_ids", side_effect=lambda kind, _id: ["a" * 64] if kind == "container" else []):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request())
        self.assertEqual(str(failure.exception), "ORPHAN_INSTANCE_RESOURCES_PRESENT")
        (self.root / "tasks" / TASK / "status.json").write_text(json.dumps({"status": "running"}))
        (self.root / "tasks" / TASK / "status.json").chmod(0o600)
        with patch.object(MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=3)):
            with self.assertRaises(MODULE.Rejected) as active:
                MODULE.remove(self.request())
        self.assertEqual(str(active.exception), "ORPHAN_INSTANCE_TASK_ACTIVE")

    def test_confirmation_and_revision_are_checked_before_mutation(self):
        request = self.request()
        request["typedName"] = "wrong"
        with self.assertRaises(MODULE.Rejected):
            MODULE.remove(request)
        request = self.request()
        request["expectedRegistryRevision"] = 2
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.remove(request)
        self.assertEqual(str(failure.exception), "REGISTRY_CHANGED")
        self.assertEqual(json.loads((self.root / "registry.json").read_text())["registryRevision"], 3)

    def test_refuses_entries_already_in_delete_lifecycle(self):
        registry = json.loads((self.root / "registry.json").read_text())
        registry["instances"][0]["lifecycle"] = "deleting"
        (self.root / "registry.json").write_text(json.dumps(registry))
        (self.root / "registry.json").chmod(0o600)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.remove(self.request())
        self.assertEqual(str(failure.exception), "INSTANCE_CLEANUP_NOT_ALLOWED")

    def test_script_entrypoint_reads_request_and_returns_json(self):
        output = StringIO()
        with patch.object(MODULE.sys, "argv", ["cleanup-orphan-instance.py", "request"]), \
             patch.object(MODULE.os, "read", return_value=b"{}"), \
             patch.object(MODULE, "remove", return_value={"removed": True}), \
             redirect_stdout(output):
            MODULE.main({})
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "data": {"removed": True}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
