"""Test persistent install task state validation and transitions."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("docker_install_control",PROJECT/"remote-scripts/actions/docker-install-control.py")
CONTROL=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="docker-task-state-")
        self.root=Path(self.temp.name)
        CONTROL.BOOTSTRAP=self.root
        CONTROL.TASKS=self.root/"docker-install"
        CONTROL.TASKS.mkdir(mode=0o700)
        self.task_id="11111111-1111-4111-8111-111111111111"
        self.task=CONTROL.TASKS/self.task_id
        self.task.mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_root_owned_shape_with_current_service_identity(self):
        state={"schemaVersion":1,"taskId":self.task_id,"sequence":1,"status":"queued","phase":"accepted","messageCode":None,"startedAtUnixSeconds":1,"updatedAtUnixSeconds":1,"installedVersions":{}}
        (self.task/"status.json").write_text(json.dumps(state))
        (self.task/"status.json").chmod(0o600)
        self.assertEqual(CONTROL.read_state(self.task_id)["status"],"queued")

    def test_querying_unaccepted_operation_does_not_return_old_failure(self):
        old = {"taskId":self.task_id,"status":"failed","phase":"failed","messageCode":"INSTALL_FAILED"}
        (self.task / "status.json").write_text(json.dumps(old))
        (self.task / "status.json").chmod(0o600)
        self.assertEqual(CONTROL.status(), old)
        self.assertIsNone(CONTROL.status("22222222-2222-4222-8222-222222222222"))
        self.assertEqual(CONTROL.status(self.task_id), old)

    def test_exact_query_does_not_hide_missing_status_or_unsafe_identity(self):
        for task_id, expected in [(self.task_id,"INSTALL_TASK_STATE_INVALID"), ("../outside","INSTALL_TASK_ID_INVALID")]:
            with self.assertRaises(CONTROL.Rejected) as failure:
                CONTROL.status(task_id)
            self.assertEqual(str(failure.exception), expected)

    def test_exact_query_reconciles_the_requested_running_task(self):
        state={"taskId":self.task_id,"status":"running","phase":"rechecking","sequence":1,"updatedAtUnixSeconds":1}
        (self.task / "status.json").write_text(json.dumps(state))
        (self.task / "status.json").chmod(0o600)
        with patch.object(CONTROL.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout="inactive")):
            result=CONTROL.status(self.task_id)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["sequence"],2)

    def test_rejects_symlink_and_broadly_readable_status(self):
        outside=self.root/"outside"
        outside.write_text("{}")
        (self.task/"status.json").symlink_to(outside)
        with self.assertRaises(CONTROL.Rejected):
            CONTROL.read_state(self.task_id)

        (self.task/"status.json").unlink()
        (self.task/"status.json").write_text("{}")
        (self.task/"status.json").chmod(stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP)
        with self.assertRaises(CONTROL.Rejected):
            CONTROL.read_state(self.task_id)

    def test_admission_publishes_sequence_one_before_starting_worker(self):
        self.task.rmdir()
        host_id = "33333333-3333-4333-8333-333333333333"
        request = {"operationId": self.task_id, "planId": "22222222-2222-4222-8222-222222222222",
                   "hostId": host_id, "snapshotDigest": "a" * 64,
                   "expiresAtUnixSeconds": int(CONTROL.time.time()) + 300}
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        def launch(command, **_kwargs):
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["sequence"], 1)
            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["hostId"], host_id)
            self.assertEqual(command[0], "systemd-run")
            return SimpleNamespace(returncode=0)
        # Simulate root ownership only in this fixture; never run systemd or an installer.
        with patch.object(CONTROL.os, "geteuid", return_value=0), \
             patch.object(Path, "lstat", lambda path: root_owned(original_lstat(path))), \
             patch.object(CONTROL.os, "fstat", lambda fd: root_owned(original_fstat(fd))), \
             patch.object(CONTROL, "inspect", return_value={"status":"ready", "hostId":host_id, "snapshotDigest":"a"*64}, create=True) as inspect, \
             patch.object(CONTROL.subprocess, "run", side_effect=launch) as runner:
            accepted = CONTROL.start(request, "# fixed worker", "# fixed preflight")
            self.assertEqual(accepted["sequence"], 1)
            self.assertEqual(CONTROL.start(request, "# fixed worker", "# fixed preflight"), accepted)
            self.assertEqual(runner.call_count, 1)
            retry = {**request, "operationId":"44444444-4444-4444-8444-444444444444"}
            cases = [
                ({**retry,"expiresAtUnixSeconds":1}, inspect.return_value.copy(), "PLAN_EXPIRED"),
                (retry, {**inspect.return_value,"hostId":"55555555-5555-4555-8555-555555555555"}, "INSTALL_HOST_CHANGED"),
                (retry, {**inspect.return_value,"status":"blocked"}, "INSTALL_PREFLIGHT_BLOCKED"),
                (retry, {**inspect.return_value,"snapshotDigest":"b"*64}, "INSTALL_SNAPSHOT_CHANGED"),
            ]
            for rejected_request, preflight, code in cases:
                inspect.return_value = preflight
                with self.subTest(code=code), self.assertRaises(CONTROL.Rejected) as failure:
                    CONTROL.start(rejected_request,"# worker","# preflight")
                self.assertEqual(str(failure.exception),code)
                self.assertFalse((CONTROL.TASKS / retry["operationId"]).exists())
            self.assertEqual(runner.call_count,1)


if __name__=="__main__":
    unittest.main(verbosity=2)
