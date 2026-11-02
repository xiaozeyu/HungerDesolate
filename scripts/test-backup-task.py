"""Test durable backup-task admission and target-bound status reads."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backup_task", PROJECT / "remote-scripts/actions/backup-task.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
OPERATION = "44444444-4444-4444-8444-444444444444"
BACKUP = "55555555-5555-4555-8555-555555555555"


class BackupTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup-task-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.root.mkdir(mode=0o700)
        (self.root / "locks").mkdir(mode=0o700)
        registry_path = self.root / "registry.json"
        registry_path.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "registryRevision": 0, "instances": [{"instanceId": INSTANCE, "lifecycle": "ready",
            "pendingTaskId": None}], "heavyQueue": []}))
        registry_path.chmod(0o600)
        self.tasks = self.root / "backup-tasks"
        self.root_patch = patch.object(MODULE, "ROOT", self.root)
        self.tasks_patch = patch.object(MODULE, "TASKS", self.tasks)
        self.euid_patch = patch.object(MODULE.os, "geteuid", return_value=0)
        original_lstat = Path.lstat
        original_fstat = os.fstat
        def root_lstat(path):
            values = list(original_lstat(path))
            values[4] = 0
            return os.stat_result(values)
        def root_fstat(fd):
            values = list(original_fstat(fd))
            values[4] = 0
            return os.stat_result(values)
        self.lstat_patch = patch.object(Path, "lstat", root_lstat)
        self.fstat_patch = patch.object(os, "fstat", root_fstat)
        self.verify_mock = Mock(return_value=(None, None, [], "running"))
        self.verify_patch = patch.object(MODULE, "verify_instance", self.verify_mock)
        self.launch_mock = Mock(return_value=types.SimpleNamespace(returncode=0))
        self.launch_patch = patch.object(MODULE.subprocess, "run", self.launch_mock)
        for item in (self.root_patch, self.tasks_patch, self.euid_patch, self.lstat_patch,
                     self.fstat_patch, self.verify_patch, self.launch_patch):
            item.start()
        self.request = {"schemaVersion": 1, "action": "start", "requestId": REQUEST_ID,
            "operationId": OPERATION, "backupId": BACKUP, "hostId": HOST, "instanceId": INSTANCE,
            "expectedRevision": 4, "captureMode": "cold", "reason": "手动创建"}

    def tearDown(self):
        for item in (self.launch_patch, self.verify_patch, self.fstat_patch, self.lstat_patch,
                     self.euid_patch, self.tasks_patch, self.root_patch):
            item.stop()
        self.temp.cleanup()

    def test_admission_persists_task_before_start_and_is_idempotent(self):
        worker_bundle = "worker bundle from desktop"
        first = MODULE.start(self.request, "archive source", worker_bundle)
        again = MODULE.start(dict(self.request, requestId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), "archive source")
        self.assertEqual(first["taskId"], OPERATION)
        self.assertEqual(first["operation"], "backupCreate")
        self.assertEqual(first["status"], "queued")
        registry = json.loads((self.root / "registry.json").read_text())
        self.assertEqual(registry["instances"][0]["pendingTaskId"], OPERATION)
        self.assertEqual(registry["heavyQueue"][0]["taskId"], OPERATION)
        self.assertEqual(registry["heavyQueue"][0]["operation"], "backupCreate")
        self.assertEqual(MODULE.queue_position(OPERATION), 1)
        self.assertEqual(again["sequence"], first["sequence"])
        self.assertTrue((self.tasks / OPERATION / "request.json").is_file())
        self.assertTrue((self.tasks / OPERATION / "worker.py").is_file())
        self.assertEqual((self.tasks / OPERATION / "worker.py").read_text(), worker_bundle)
        self.launch_mock.assert_called_once()

    def test_reused_operation_with_different_request_is_rejected(self):
        MODULE.start(self.request, "archive source")
        changed = dict(self.request, reason="不同请求")
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.start(changed, "archive source")
        self.assertEqual(failure.exception.code, "OPERATION_ID_REUSED")

    def test_status_checks_the_full_task_target(self):
        MODULE.start(self.request, "archive source")
        status_request = {"schemaVersion": 1, "requestId": REQUEST_ID,
            "operationId": OPERATION, "hostId": HOST, "instanceId": INSTANCE}
        state = MODULE.status(status_request)
        self.assertEqual(state["taskId"], OPERATION)
        wrong = dict(status_request, instanceId="66666666-6666-4666-8666-666666666666")
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.status(wrong)
        self.assertEqual(failure.exception.code, "BACKUP_TARGET_MISMATCH")

    def test_active_backup_blocks_a_second_operation(self):
        MODULE.start(self.request, "archive source")
        second = dict(self.request, requestId="77777777-7777-4777-8777-777777777777",
                      operationId="88888888-8888-4888-8888-888888888888",
                      backupId="99999999-9999-4999-8999-999999999999")
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.start(second, "archive source")
        self.assertEqual(failure.exception.code, "BACKUP_TASK_BUSY")

    def test_cancel_request_is_written_for_an_active_backup(self):
        task_id = "77777777-7777-4777-8777-777777777777"
        directory = self.tasks / task_id
        directory.mkdir(parents=True, mode=0o700)
        MODULE.atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": task_id,
            "operationId": task_id, "target": {"hostId": HOST, "instanceId": INSTANCE},
            "operation": "backupCreate", "status": "queued", "stage": "queued", "sequence": 1,
            "createdAt": MODULE.now(), "cancelAllowed": True})
        request = {"schemaVersion": 1, "action": "cancel", "requestId": REQUEST_ID,
                   "operationId": task_id, "hostId": HOST, "instanceId": INSTANCE}
        state = MODULE.cancel(request)
        self.assertEqual(state["status"], "queued")
        self.assertTrue((directory / "cancel.request").is_file())

    def test_quasihot_admission_does_not_require_verified_docker_runtime(self):
        self.request["captureMode"] = "quasiHot"
        MODULE.start(self.request, "archive source")
        self.verify_mock.assert_called_with(self.request, True)

    def test_backup_restorability_is_verified_from_world_snapshot_and_release_artifacts(self):
        release_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        image_digest = "sha256:" + "a" * 64
        release = {"releaseId": release_id, "imageDigest": image_digest, "verified": True}
        release_dir = self.root / "staging" / release_id
        release_dir.mkdir(parents=True, mode=0o700)
        (release_dir / "release-metadata.json").write_text(json.dumps({"schemaVersion": 1, **release}))
        (release_dir / "release-metadata.json").chmod(0o600)
        release_dir.chmod(0o700)
        catalog_dir = self.root / "releases"
        catalog_dir.mkdir(mode=0o700)
        (catalog_dir / "catalog.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST, "releases": [release]}))
        (catalog_dir / "catalog.json").chmod(0o600)
        instance = self.root / "instances" / INSTANCE
        desired_dir = instance / "desired"
        desired_dir.mkdir(parents=True, mode=0o700)
        settings = {"gameMode": "survival"}
        digest = "sha256:" + __import__("hashlib").sha256(MODULE.canonical(settings)).hexdigest()
        desired = {"schemaVersion": 1, "instanceId": INSTANCE, "revision": 4,
                   "settings": settings, "secrets": {"tokenRef": OPERATION}}
        deployed = {"schemaVersion": 1, "instanceId": INSTANCE, "revision": 4,
                    "settings": settings, "digest": digest}
        (desired_dir / "world.json").write_text(json.dumps(desired))
        (desired_dir / "world.json").chmod(0o600)
        (instance / "deployed-world.json").write_text(json.dumps(deployed))
        (instance / "deployed-world.json").chmod(0o600)
        with patch.object(MODULE.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout=image_digest)):
            status, observed = MODULE.backup_restorability(
                instance, {"instanceId": INSTANCE, "expectedRevision": 4}, release_id)
        self.assertEqual(status, "restorable")
        self.assertEqual(observed["releaseId"], release_id)

    def test_injected_request_is_accepted_by_remote_main_entrypoint(self):
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.main({"schemaVersion": 1, "action": "listTasks", "requestId": REQUEST_ID,
                         "hostId": HOST, "instanceId": INSTANCE})
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"], [])

    def test_status_request_emits_json_task_envelope(self):
        MODULE.start(self.request, "archive source")
        output = io.StringIO()
        status_request = {"schemaVersion": 1, "action": "status", "requestId": REQUEST_ID,
            "operationId": OPERATION, "hostId": HOST, "instanceId": INSTANCE}
        with redirect_stdout(output):
            MODULE.main(status_request)
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["taskId"], OPERATION)
        self.assertEqual(envelope["data"]["status"], "queued")


class BackupSanitizerTests(unittest.TestCase):
    def test_sanitizes_cluster_secrets_and_access_fields(self):
        with tempfile.TemporaryDirectory(prefix="backup-sanitize-") as temporary:
            path = Path(temporary) / "cluster.ini"
            path.write_text("[NETWORK]\ncluster_password = secret\n[SHARD]\ncluster_key = 0123456789abcdef0123456789abcdef\n[GAMEPLAY]\nwhitelist_slots = 3\n")
            data = MODULE.sanitize_cluster_ini(path).decode()
            self.assertNotIn("cluster_password", data)
            self.assertNotIn("cluster_key", data)
            self.assertNotIn("whitelist_slots", data)

    def test_sanitizes_world_access_policy_and_recomputes_digest(self):
        with tempfile.TemporaryDirectory(prefix="backup-world-") as temporary:
            path = Path(temporary) / "world.json"
            settings = {"advanced": {"adminList": ["admin"], "whitelist": ["white"],
                                     "blocklist": ["block"], "whitelistSlots": 2}}
            path.write_text(json.dumps({"schemaVersion": 1, "settings": settings,
                                        "digest": MODULE.digest(settings)}))
            value = json.loads(MODULE.sanitize_world_snapshot(path))
            self.assertEqual(value["settings"]["advanced"]["adminList"], [])
            self.assertEqual(value["settings"]["advanced"]["whitelist"], [])
            self.assertEqual(value["settings"]["advanced"]["blocklist"], [])
            self.assertEqual(value["settings"]["advanced"]["whitelistSlots"], 0)
            self.assertEqual(value["digest"], MODULE.digest(value["settings"]))


if __name__ == "__main__":
    unittest.main()
