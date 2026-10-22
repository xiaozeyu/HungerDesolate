"""Inject cold-backup stop, finalize, worker-death and identity failures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
TASK_SPEC = importlib.util.spec_from_file_location(
    "backup_task_worker", PROJECT / "remote-scripts/actions/backup-task.py")
TASK = importlib.util.module_from_spec(TASK_SPEC)
TASK_SPEC.loader.exec_module(TASK)
ARCHIVE = (PROJECT / "remote-scripts/actions/backup-archive.py").read_text()
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
TASK_ID = "33333333-3333-4333-8333-333333333333"
BACKUP_ID = "44444444-4444-4444-8444-444444444444"


class BackupWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup-worker-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.root.mkdir(mode=0o700)
        self.tasks = self.root / "backup-tasks"
        task_dir = self.tasks / TASK_ID
        (task_dir / "locks").mkdir(parents=True)
        (self.root / "locks").mkdir()
        instance = self.root / "instances" / INSTANCE
        (instance / "locks").mkdir(parents=True)
        (instance / "data" / "Master").mkdir(parents=True)
        (instance / "config" / "Cluster_1" / "Master").mkdir(parents=True)
        (instance / "data" / "Master" / "save.zip").write_bytes(b"world-state")
        (instance / "config" / "Cluster_1" / "Master" / "server.ini").write_text("[NETWORK]\nserver_port=11000\n")
        (instance / "config" / "Cluster_1" / "cluster_token.txt").write_text("secret-token")
        (instance / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "instanceId": INSTANCE, "configRevision": 4, "composeProject": "fh_" + INSTANCE.replace("-", ""),
            "cavesEnabled": False}))
        (instance / "active-release.json").write_text(json.dumps({"dstReleaseId": "55555555-5555-4555-8555-555555555555"}))
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (self.root / "registry.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready"}]}))
        for path in [self.root, self.root / "locks", self.tasks, task_dir, instance,
                     instance / "locks", instance / "data", instance / "data" / "Master",
                     instance / "config", instance / "config" / "Cluster_1",
                     instance / "config" / "Cluster_1" / "Master"]:
            path.chmod(0o700)
        for path in [instance / "manifest.json", instance / "active-release.json",
                     self.root / "host.json", self.root / "registry.json",
                     instance / "data" / "Master" / "save.zip",
                     instance / "config" / "Cluster_1" / "Master" / "server.ini",
                     instance / "config" / "Cluster_1" / "cluster_token.txt"]:
            path.chmod(0o600)
        self.instance = instance
        self.task_dir = task_dir
        self.request = {"schemaVersion": 1, "action": "start", "requestId": TASK_ID,
            "operationId": TASK_ID, "backupId": BACKUP_ID, "hostId": HOST, "instanceId": INSTANCE,
            "expectedRevision": 4, "captureMode": "cold", "reason": "手动创建"}
        request_record = dict(self.request)
        request_record["requestDigest"] = TASK.operation_digest(self.request)
        TASK.atomic_json(task_dir / "request.json", request_record)
        TASK.atomic_json(task_dir / "archive-source.json", {"source": ARCHIVE})
        TASK.atomic_json(task_dir / "status.json", self.initial_status("queued", "queued"))
        TASK.atomic_bytes(task_dir / "journal.jsonl", TASK.canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": TASK.now()}) + b"\n")
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_lstat(path):
            values = list(original_lstat(path))
            values[4] = 0
            return os.stat_result(values)
        def root_fstat(fd):
            values = list(original_fstat(fd))
            values[4] = 0
            return os.stat_result(values)
        self.patches = [
            patch.object(TASK, "ROOT", self.root), patch.object(TASK, "TASKS", self.tasks),
            patch.object(TASK.os, "geteuid", return_value=0), patch.object(Path, "lstat", root_lstat),
            patch.object(TASK.os, "fstat", root_fstat),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def initial_status(status, stage):
        return {"schemaVersion": 1, "taskId": TASK_ID, "operationId": TASK_ID,
            "target": {"hostId": HOST, "instanceId": INSTANCE}, "operation": "backupCreate",
            "status": status, "stage": stage, "lastCompletedStage": None,
            "progress": {"kind": "indeterminate"}, "sequence": 1,
            "createdAt": TASK.now(), "startedAt": None, "finishedAt": None,
            "cancelAllowed": False, "observation": "live", "result": None, "error": None}

    def verify(self, states):
        iterator = iter(states)
        return lambda _request, allow_unknown=False, allow_degraded=False: (self.instance,
            TASK.read_json(self.instance / "manifest.json"), ["master"], next(iterator))

    def subprocess(self, exit_codes):
        iterator = iter(exit_codes)
        calls = []
        def run(args, **_kwargs):
            calls.append(args)
            return types.SimpleNamespace(returncode=next(iterator))
        return Mock(side_effect=run), calls

    def test_cold_stop_failure_attempts_restore_after_partial_stop(self):
        command, calls = self.subprocess([1, 0])
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["running", "running", "degraded", "running"])), \
             patch.object(TASK.subprocess, "run", command):
            result = TASK.worker(TASK_ID)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "BACKUP_STOP_FAILED")
        self.assertTrue(any("start" in call for call in calls))

    def test_cold_backup_stops_a_verified_degraded_runtime_without_restart_loop(self):
        command, calls = self.subprocess([0])
        with patch.object(TASK, "verify_instance", side_effect=self.verify(
                ["degraded", "degraded", "stopped", "stopped", "running"])), \
             patch.object(TASK.subprocess, "run", command):
            result = TASK.worker(TASK_ID)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(any("stop" in call for call in calls))
        self.assertFalse(any("start" in call for call in calls))

    def test_metadata_finalize_failure_still_restarts_original_server(self):
        command, calls = self.subprocess([0, 0])
        atomic_json = TASK.atomic_json
        def fail_metadata(path, value):
            if Path(path).name == "metadata.json" and value.get("verification") == "verified":
                raise OSError("injected metadata failure")
            atomic_json(path, value)
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["running", "running", "stopped", "stopped", "running"])), \
             patch.object(TASK.subprocess, "run", command), patch.object(TASK, "atomic_json", side_effect=fail_metadata):
            result = TASK.worker(TASK_ID)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("start" in call for call in calls))
        metadata = json.loads((self.task_dir / "backup-staging" / "metadata.json").read_text())
        self.assertEqual(metadata["verification"], "failed")
        self.assertIsNone(metadata["checksum"])
        self.assertFalse((self.root / "backups" / INSTANCE / BACKUP_ID).exists())

    def test_interrupted_worker_recovers_running_instance_before_marking_interrupted(self):
        TASK.atomic_json(self.task_dir / "status.json", self.initial_status("running", "backup"))
        TASK.atomic_json(self.task_dir / "execution.json", {"schemaVersion": 1, "operationId": TASK_ID,
            "captureMode": "cold", "runtimeBefore": "running", "stopAttempted": True, "runtimeRestored": False})
        command, calls = self.subprocess([0])
        systemctl = Mock(return_value=types.SimpleNamespace(returncode=3))
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["stopped", "stopped", "running"])), \
             patch.object(TASK.subprocess, "run", side_effect=lambda args, **kwargs:
                 systemctl(args, **kwargs) if args[0] == "systemctl" else command(args, **kwargs)):
            result = TASK.status({"schemaVersion": 1, "requestId": TASK_ID,
                "operationId": TASK_ID, "hostId": HOST, "instanceId": INSTANCE})
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["runtimeRecovery"], "restored", result["error"])
        self.assertTrue(any("start" in call for call in calls))

    def test_task_identity_mismatch_is_rejected_before_status_reconciliation(self):
        with self.assertRaises(TASK.Rejected) as failure:
            TASK.status({"schemaVersion": 1, "requestId": TASK_ID, "operationId": TASK_ID,
                "hostId": HOST, "instanceId": "66666666-6666-4666-8666-666666666666"})
        self.assertEqual(failure.exception.code, "BACKUP_TARGET_MISMATCH")

    def test_existing_backup_id_is_never_overwritten(self):
        existing = self.root / "backups" / INSTANCE / BACKUP_ID
        existing.mkdir(parents=True)
        (self.root / "backups").chmod(0o700)
        (self.root / "backups" / INSTANCE).chmod(0o700)
        existing.chmod(0o700)
        marker = existing / "keep"
        marker.write_text("existing backup")
        command, calls = self.subprocess([])
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["running", "running"])), \
             patch.object(TASK.subprocess, "run", command):
            result = TASK.worker(TASK_ID)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "BACKUP_ID_EXISTS")
        self.assertEqual(marker.read_text(), "existing backup")
        self.assertEqual(calls, [])

    def test_failed_restart_can_be_reconciled_without_losing_original_error(self):
        first_command, _ = self.subprocess([0, 1])
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["running", "running", "stopped", "stopped"])), \
             patch.object(TASK.subprocess, "run", first_command):
            failed = TASK.worker(TASK_ID)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "BACKUP_RESTART_FAILED")
        recovery_command, calls = self.subprocess([0])
        systemctl = Mock(return_value=types.SimpleNamespace(returncode=3))
        with patch.object(TASK, "verify_instance", side_effect=self.verify(["stopped", "stopped", "running"])), \
             patch.object(TASK.subprocess, "run", side_effect=lambda args, **kwargs:
                 systemctl(args, **kwargs) if args[0] == "systemctl" else recovery_command(args, **kwargs)):
            reconciled = TASK.status({"schemaVersion": 1, "requestId": TASK_ID,
                "operationId": TASK_ID, "hostId": HOST, "instanceId": INSTANCE})
        self.assertEqual(reconciled["status"], "failed")
        self.assertEqual(reconciled["runtimeRecovery"], "restored")
        self.assertEqual(reconciled["error"]["code"], "BACKUP_RESTART_FAILED")
        self.assertTrue(any("start" in call for call in calls))

    def test_preflight_worker_exception_is_persisted_as_failed_task(self):
        TASK.atomic_json(self.task_dir / "archive-source.json", {"source": "raise RuntimeError('bad bundle')"})
        result = TASK.worker(TASK_ID)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "BACKUP_WORKER_FAILED")

    def test_queued_task_without_execution_record_is_a_known_worker_start_failure(self):
        systemctl = Mock(return_value=types.SimpleNamespace(returncode=3))
        with patch.object(TASK.subprocess, "run", systemctl):
            result = TASK.status({"schemaVersion": 1, "requestId": TASK_ID,
                "operationId": TASK_ID, "hostId": HOST, "instanceId": INSTANCE})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "BACKUP_WORKER_NOT_STARTED")
        self.assertFalse(result["error"]["remoteChanged"])


if __name__ == "__main__":
    unittest.main()
