"""Test the remote instance creation admission transaction in isolation."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instance_create_control", PROJECT / "remote-scripts/actions/instance-create-control.py")
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)

HOST = "11111111-1111-4111-8111-111111111111"
PLAN = "22222222-2222-4222-8222-222222222222"
OPERATION = "33333333-3333-4333-8333-333333333333"
RELEASE = "44444444-4444-4444-8444-444444444444"


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-create-admission-")
        root = Path(self.temp.name) / "famine-hub"
        root.mkdir(mode=0o700)
        (root / "locks").mkdir(mode=0o700)
        (root / "releases").mkdir(mode=0o700)
        (root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        self.registry = {"schemaVersion": 1, "hostId": HOST, "registryRevision": 3,
                         "instances": [], "portReservations": [], "operations": []}
        (root / "registry.json").write_text(json.dumps(self.registry))
        release = {"releaseId": RELEASE, "platform": "linux/amd64", "image": "registry.example/dst",
                   "imageDigest": "sha256:" + "a" * 64, "dstBuild": "build-2026",
                   "artifactDigest": "sha256:" + "b" * 64, "scriptVersion": "scripts-1",
                   "verified": True, "runtimeVerified": False,
                   "commands": {"master": ["/opt/dst", "master"], "caves": ["/opt/dst", "caves"]}}
        catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [release]}
        (root / "releases" / "catalog.json").write_text(json.dumps(catalog))
        self.root = root
        self.request = {"action": "admit", "schemaVersion": 1, "operationId": OPERATION,
                        "planId": PLAN, "hostId": HOST, "expectedRegistryRevision": 3,
                        "input": {"displayName": " 生存一服 ", "autostart": True,
                                   "startAfterCreate": True, "portPolicy": "auto",
                                   "cavesEnabled": False,
                                   "releaseId": RELEASE,
                                   "releaseCatalogDigest": CONTROL.digest(catalog),
                                   "expectedPorts": CONTROL.allocate(set(), False)}}
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        self.patches = [
            patch.object(CONTROL, "ROOT", root),
            patch.object(CONTROL, "CATALOG", root / "releases" / "catalog.json"),
            patch.object(CONTROL, "LOCKS", root / "locks"),
            patch.object(CONTROL, "LOCK_PATH", root / "locks" / "registry.lock"),
            patch.object(CONTROL, "OWNER_PATH", root / "locks" / "registry-owner.json"),
            patch.object(CONTROL, "socket_ports", return_value=set()),
            patch.object(CONTROL, "docker_ports", return_value=set()),
            patch.object(CONTROL.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", lambda path: root_owned(original_lstat(path))),
            patch.object(CONTROL.os, "fstat", lambda fd: root_owned(original_fstat(fd))),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_admission_writes_creating_entry_and_full_reservation(self):
        result = CONTROL.admit(self.request)
        self.assertEqual(result["registryRevision"], 4)
        saved = json.loads((self.root / "registry.json").read_text())
        self.assertEqual(saved["registryRevision"], 4)
        self.assertEqual(saved["instances"][0]["lifecycle"], "creating")
        self.assertEqual(saved["instances"][0]["pendingTaskId"], OPERATION)
        self.assertEqual(len(saved["portReservations"]), 3)
        self.assertEqual(saved["portReservations"][0]["instanceId"], result["instanceId"])
        self.assertEqual(saved["operations"][0]["operationId"], OPERATION)

    def test_same_operation_replays_without_allocating_a_second_instance(self):
        first = CONTROL.admit(self.request)
        second = CONTROL.admit(self.request)
        self.assertEqual(second, first)
        saved = json.loads((self.root / "registry.json").read_text())
        self.assertEqual(len(saved["instances"]), 1)
        self.assertEqual(len(saved["portReservations"]), 3)
        self.assertEqual(saved["registryRevision"], 4)

    def test_operation_reuse_with_different_input_is_rejected(self):
        CONTROL.admit(self.request)
        changed = json.loads(json.dumps(self.request))
        changed["input"]["displayName"] = "另一服"
        with self.assertRaises(CONTROL.Rejected) as failure:
            CONTROL.admit(changed)
        self.assertEqual(str(failure.exception), "OPERATION_ID_REUSED")

    def test_registry_revision_and_preview_ports_are_rechecked(self):
        stale = json.loads(json.dumps(self.request))
        stale["expectedRegistryRevision"] = 2
        with self.assertRaises(CONTROL.Rejected) as failure:
            CONTROL.admit(stale)
        self.assertEqual(str(failure.exception), "REGISTRY_CHANGED")

        mismatched = json.loads(json.dumps(self.request))
        mismatched["operationId"] = "44444444-4444-4444-8444-444444444444"
        mismatched["input"]["expectedPorts"][0]["hostPort"] += 1
        with self.assertRaises(CONTROL.Rejected) as failure:
            CONTROL.admit(mismatched)
        self.assertEqual(str(failure.exception), "PLAN_STALE")

    def test_existing_registry_reservation_is_considered_before_allocation(self):
        existing = CONTROL.allocate(set(), False)
        self.registry["instances"] = [{"instanceId": "55555555-5555-4555-8555-555555555555", "lifecycle": "ready"}]
        self.registry["portReservations"] = [{"hostId": HOST, "instanceId": self.registry["instances"][0]["instanceId"], **item}
                                             for item in existing]
        (self.root / "registry.json").write_text(json.dumps(self.registry))
        request = json.loads(json.dumps(self.request))
        request["operationId"] = "66666666-6666-4666-8666-666666666666"
        request["input"]["expectedPorts"] = [{**item, "hostPort": item["hostPort"] + 1, "containerPort": item["containerPort"] + 1}
                                               for item in existing]
        result = CONTROL.admit(request)
        self.assertEqual(result["ports"][0]["hostPort"], 11001)

    def test_lock_contention_is_rejected_without_touching_registry(self):
        fd, token, generation = CONTROL.lock_registry(OPERATION)
        try:
            with self.assertRaises(CONTROL.Rejected) as failure:
                CONTROL.admit(self.request)
            self.assertEqual(str(failure.exception), "REGISTRY_BUSY")
            self.assertEqual(json.loads((self.root / "registry.json").read_text())["registryRevision"], 3)
        finally:
            CONTROL.release_registry(fd, token, generation, OPERATION)

    def test_failed_publish_does_not_leave_a_second_operation_record(self):
        with patch.object(CONTROL, "atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                CONTROL.admit(self.request)
        self.assertEqual(json.loads((self.root / "registry.json").read_text())["registryRevision"], 3)

    def test_registry_reads_reject_symlink_swap(self):
        original_open = CONTROL.os.open
        def replace_before_open(path, flags, *args):
            if Path(path) == self.root / "registry.json":
                target = self.root / "outside.json"
                target.write_text(json.dumps(self.registry))
                (self.root / "registry.json").unlink()
                (self.root / "registry.json").symlink_to(target)
            return original_open(path, flags, *args)
        with patch.object(CONTROL.os, "open", side_effect=replace_before_open):
            with self.assertRaises((CONTROL.Rejected, OSError)) as failure:
                CONTROL.admit(self.request)
        if isinstance(failure.exception, CONTROL.Rejected):
            self.assertEqual(str(failure.exception), "CREATE_FILE_INVALID")

    def test_creation_accepts_legacy_delete_receipt_without_losing_history_or_idempotency(self):
        spec = importlib.util.spec_from_file_location("instance_delete", PROJECT / "remote-scripts/actions/instance-delete.py")
        delete_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(delete_module)
        delete_request = {"operationId": "66666666-6666-4666-8666-666666666666", "hostId": HOST,
                          "instanceId": "55555555-5555-4555-8555-555555555555", "expectedRegistryRevision": 2,
                          "planId": "a" * 64, "typedName": "已删除专服"}
        delete_id, delete_digest = delete_module.operation_digest(delete_request)
        prior = {"operationId": delete_id, "payloadDigest": delete_digest.removeprefix("sha256:"),
                 "result": {"hostId": HOST, "instanceId": delete_request["instanceId"], "removed": True}}
        self.registry["operations"] = [prior]
        (self.root / "registry.json").write_text(json.dumps(self.registry))
        first = CONTROL.admit(self.request)
        second = CONTROL.admit(self.request)
        self.assertEqual(first, second)
        saved = json.loads((self.root / "registry.json").read_text())
        self.assertEqual(saved["operations"][0], prior)
        self.assertEqual(len(saved["operations"]), 2)
        self.assertEqual(len(saved["instances"]), 1)
        self.assertEqual(len(saved["portReservations"]), 3)
        self.assertEqual(saved["registryRevision"], 4)

    def test_malformed_operation_digest_is_still_rejected_without_writes(self):
        for invalid_digest in ["", "sha256:bad", "a" * 63, "a" * 65, "A" * 64, None]:
            with self.subTest(digest=invalid_digest):
                self.registry["operations"] = [{"operationId": OPERATION, "payloadDigest": invalid_digest, "result": {}}]
                path = self.root / "registry.json"
                path.write_text(json.dumps(self.registry))
                before = path.read_bytes()
                with self.assertRaises(CONTROL.Rejected) as failure:
                    CONTROL.admit(self.request)
                self.assertEqual(str(failure.exception), "CREATE_OPERATION_STATE_INVALID")
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
