"""Run the production initializer against private disposable directories.

Includes process death at filesystem publication boundaries, concurrent clients,
receipt replay, stale plans, permissions and hostile filesystem types. No /var/lib
or Docker writes. Linux platform is emulated for filesystem tests on macOS.
"""
import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("initializer", PROJECT / "remote-scripts/actions/initialize-host.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTEXT = multiprocessing.get_context("fork")


def invoke(root, request):
    with patch.object(MODULE, "ROOT", str(root)), patch.object(MODULE.sys, "platform", "linux"):
        return MODULE.run(request)


def planned(root):
    preview = invoke(root, {"action": "preview"})
    return {"action": "initialize", "operationId": str(uuid.uuid4()), "payload": {
        "hostId": str(uuid.uuid4()), "snapshot": preview["snapshot"],
        "expiresAtUnixSeconds": int(time.time()) + 600,
        "serverId": str(uuid.uuid4()), "recordVersion": 1,
        "fingerprint": "SHA256:test", "scriptDigest": "fixture",
    }}


def crashing_worker(root, request, boundary):
    count = 0
    def wrap(original):
        def execute(*args, **kwargs):
            nonlocal count
            result = original(*args, **kwargs)
            count += 1
            if count == boundary:
                os._exit(91)
            return result
        return execute
    with patch.object(os, "fsync", wrap(os.fsync)), patch.object(os, "mkdir", wrap(os.mkdir)), patch.object(os, "link", wrap(os.link)), patch.object(os, "rename", wrap(os.rename)), patch.object(os, "unlink", wrap(os.unlink)):
        invoke(root, request)


def competing_worker(root, request, queue):
    try:
        queue.put(invoke(root, request))
    except MODULE.Rejected as error:
        queue.put({"error": str(error)})


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hd-init-")
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "managed"

    def tearDown(self):
        self.temporary.cleanup()

    def test_preview_is_readonly_and_success_is_discoverable(self):
        request = planned(self.root)
        self.assertEqual(list(self.parent.iterdir()), [])
        result = invoke(self.root, request)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["hostId"], request["payload"]["hostId"])
        self.assertEqual(invoke(self.root, {**request, "action": "query"}), result)
        self.assertEqual(invoke(self.root, request), result)
        self.assertEqual(invoke(self.root, {"action": "preview"})["state"], "ready")
        registry = json.loads((self.root / "registry.json").read_bytes())
        self.assertEqual(registry["instances"], [])
        self.assertEqual(registry["registryRevision"], 0)
        self.assertEqual((self.root.stat().st_mode & 0o777), 0o700)
        self.assertEqual((self.root / "host.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "host.json").stat().st_nlink, 1)

    def test_empty_preprovisioned_directory_is_supported(self):
        self.root.mkdir(mode=0o700)
        inode = self.root.stat().st_ino
        invoke(self.root, planned(self.root))
        self.assertEqual(self.root.stat().st_ino, inode)

    def test_expired_changed_and_reused_plans_cannot_overwrite(self):
        request = planned(self.root)
        expired = copy.deepcopy(request)
        expired["payload"]["expiresAtUnixSeconds"] = 1
        with self.assertRaisesRegex(MODULE.Rejected, "PLAN_STALE"):
            invoke(self.root, expired)
        self.assertFalse(self.root.exists())
        self.root.mkdir(mode=0o700)
        with self.assertRaisesRegex(MODULE.Rejected, "PLAN_STALE"):
            invoke(self.root, request)
        request = planned(self.root)
        invoke(self.root, request)
        before = (self.root / "host.json").read_bytes()
        changed = copy.deepcopy(request)
        changed["payload"]["hostId"] = str(uuid.uuid4())
        with self.assertRaisesRegex(MODULE.Rejected, "OPERATION_ID_REUSED"):
            invoke(self.root, changed)
        self.assertEqual((self.root / "host.json").read_bytes(), before)
        # Replay must still work after expiration and later registry changes.
        registry = json.loads((self.root / "registry.json").read_bytes())
        registry["registryRevision"] = 1
        (self.root / "registry.json").write_text(json.dumps(registry))
        with patch.object(MODULE.time, "time", return_value=time.time() + 10000):
            self.assertEqual(invoke(self.root, request)["status"], "succeeded")

    def test_foreign_files_links_and_permissions_are_rejected(self):
        self.root.mkdir(mode=0o700)
        foreign = self.root / "world.dat"
        foreign.write_bytes(b"keep this world")
        with self.assertRaises((MODULE.Rejected, OSError)):
            planned(self.root)
        self.assertEqual(foreign.read_bytes(), b"keep this world")
        foreign.unlink()
        self.root.chmod(0o777)
        with self.assertRaisesRegex(MODULE.Rejected, "PATH_UNSAFE"):
            planned(self.root)
        self.root.chmod(0o700)
        self.root.rmdir()
        self.root.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            planned(self.root)
        self.root.unlink()
        self.root.mkdir(mode=0o700)
        (self.root / "host.json").symlink_to(self.parent / "missing")
        with self.assertRaises(OSError):
            planned(self.root)

    def test_two_clients_never_publish_two_identities(self):
        first, second = planned(self.root), planned(self.root)
        queue = CONTEXT.Queue()
        workers = [CONTEXT.Process(target=competing_worker, args=(self.root, request, queue)) for request in [first, second]]
        for worker in workers:
            worker.start()
        results = [queue.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sum(item.get("status") == "succeeded" for item in results), 1)
        self.assertEqual(sum(item.get("error") == "PLAN_STALE" for item in results), 1)

    def test_process_death_at_every_publication_boundary_is_resumable(self):
        # More boundaries than one complete run; the last children exit normally.
        for boundary in range(1, 26):
            with self.subTest(boundary=boundary):
                parent = self.parent / str(boundary)
                parent.mkdir(mode=0o700)
                root = parent / "managed"
                request = planned(root)
                worker = CONTEXT.Process(target=crashing_worker, args=(root, request, boundary))
                worker.start()
                worker.join(10)
                self.assertIn(worker.exitcode, [0, 91])
                result = invoke(root, request)
                self.assertEqual(result["hostId"], request["payload"]["hostId"])
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["host.json", "registry.json"])

    def test_partial_initialization_refuses_foreign_changes_and_queries_missing_receipts(self):
        request = planned(self.root)
        self.assertEqual(invoke(self.root, {**request, "action": "query"}), {"status": "notFound"})
        # Receipt is durable before root publication. Simulate death at root mkdir.
        original = os.mkdir
        def die_after_root(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.root.name:
                raise KeyboardInterrupt("process death")
            return result
        with patch.object(os, "mkdir", die_after_root), self.assertRaises(KeyboardInterrupt):
            invoke(self.root, request)
        (self.root / "foreign").write_bytes(b"keep")
        with self.assertRaisesRegex(MODULE.Rejected, "EXISTING_DATA"):
            invoke(self.root, request)
        self.assertEqual((self.root / "foreign").read_bytes(), b"keep")
        self.assertEqual(invoke(self.root, {**request, "action": "query"})["status"], "incomplete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
