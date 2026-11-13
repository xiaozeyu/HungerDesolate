import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_task", PROJECT / "remote-scripts/actions/update-task.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
TASK = "33333333-3333-4333-8333-333333333333"
PLAN = "44444444-4444-4444-8444-444444444444"
TARGET = "55555555-5555-4555-8555-555555555555"
REQUEST = "66666666-6666-4666-8666-666666666666"


class UpdateTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="update-task-")
        self.root = Path(self.temp.name) / "hub"
        self.tasks = self.root / "update-tasks"
        self.plans = self.root / "update-plans"
        for path in (self.root, self.tasks, self.plans, self.root / "locks"):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "registryRevision": 1,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready", "pendingTaskId": None}], "heavyQueue": []})
        self.write(self.plans / (PLAN + ".json"), {"schemaVersion": 1, "planId": PLAN,
            "target": {"serverId": REQUEST, "hostId": HOST, "instanceId": INSTANCE},
            "baseRevision": 3, "currentReleaseId": None, "targetReleaseId": TARGET,
            "currentBuild": "630000", "targetBuild": "639491",
            "createdAt": "2026-01-01T00:00:00Z", "expiresAt": "2999-01-01T00:00:00Z"})
        self.request = {"schemaVersion": 1, "action": "updateStart", "requestId": REQUEST,
            "operationId": TASK, "hostId": HOST, "instanceId": INSTANCE,
            "expectedRevision": 3, "planId": PLAN}
        original_lstat = Path.lstat
        def root_lstat(path):
            values = list(original_lstat(path))
            values[4] = 0
            return os.stat_result(values)
        self.patches = [
            patch.object(Path, "lstat", root_lstat),
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "TASKS", self.tasks),
            patch.object(MODULE, "PLANS", self.plans),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(MODULE, "safe_dir", lambda path, create=False: path.lstat() if path.exists() else None),
            patch.object(MODULE, "admit"),
            patch.object(MODULE.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)),
            patch.object(MODULE, "UPDATE_SOURCE", "print('worker')\n", create=True),
            patch.object(MODULE, "RESTORE_SOURCE", "# restore\n", create=True),
            patch.object(MODULE, "ARCHIVE_SOURCE", "# archive\n", create=True),
            patch.object(MODULE, "RELEASE_SOURCE", "# release\n", create=True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_start_writes_worker_and_source_bundle(self):
        state = MODULE.start(dict(self.request))
        self.assertEqual(state["status"], "queued")
        directory = self.tasks / TASK
        self.assertTrue((directory / "worker.py").is_file())
        self.assertEqual((directory / "restore-source.py").read_text(), "# restore\n")
        self.assertEqual((directory / "archive-source.py").read_text(), "# archive\n")
        self.assertEqual((directory / "release-source.py").read_text(), "# release\n")

    def test_expired_plan_is_rejected_before_task_creation(self):
        plan = json.loads((self.plans / (PLAN + ".json")).read_text())
        plan["expiresAt"] = "2000-01-01T00:00:00Z"
        self.write(self.plans / (PLAN + ".json"), plan)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.start(dict(self.request))
        self.assertEqual(failure.exception.code, "UPDATE_PLAN_STALE")
        self.assertFalse((self.tasks / TASK).exists())

    def test_inactive_worker_is_reconciled_as_interrupted(self):
        directory = self.tasks / TASK
        directory.mkdir(mode=0o700)
        self.write(directory / "request.json", self.request)
        self.write(directory / "status.json", {"schemaVersion": 1, "taskId": TASK, "operationId": TASK,
            "target": {"hostId": HOST, "instanceId": INSTANCE}, "operation": "instanceUpdate",
            "status": "running", "stage": "preflight", "sequence": 2,
            "createdAt": "2000-01-01T00:00:00Z", "startedAt": "2000-01-01T00:00:00Z",
            "finishedAt": None, "cancelAllowed": True, "observation": "live", "result": None, "error": None})
        (directory / "journal.jsonl").write_bytes(MODULE.canonical({"sequence": 1, "status": "running",
            "stage": "preflight", "at": "2000-01-01T00:00:00Z"}) + b"\n")
        systemctl = types.SimpleNamespace(returncode=3)
        with patch.object(MODULE.subprocess, "run", return_value=systemctl):
            state = MODULE.status({"schemaVersion": 1, "requestId": REQUEST, "hostId": HOST,
                "instanceId": INSTANCE, "operationId": TASK})
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["error"]["code"], "UPDATE_TASK_INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
