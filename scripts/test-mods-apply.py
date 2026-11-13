import importlib.util
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mods_apply", PROJECT / "remote-scripts/actions/mods-apply.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
SERVER = "33333333-3333-4333-8333-333333333333"
REQUEST = "44444444-4444-4444-8444-444444444444"
RELEASE = "55555555-5555-4555-8555-555555555555"
MOD_RELEASE = "66666666-6666-4666-8666-666666666666"
CONTENT = "a" * 64


class ModsApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mods-apply-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.instance = self.root / "instances" / INSTANCE
        for path in (self.root / "instances", self.root / "backups" / INSTANCE,
                     self.instance / "desired", self.root / "mods" / "prepared" / "123",
                     self.instance / "config" / "Cluster_1" / "Master", self.instance / "data" / "Master",
                     self.instance / "locks", self.root / "locks"):
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.chmod(0o700)
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "registryRevision": 0,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready", "pendingTaskId": None}],
            "heavyQueue": []})
        self.write(self.instance / "manifest.json", {"schemaVersion": 1, "hostId": HOST,
            "instanceId": INSTANCE, "configRevision": 3, "composeProject": "fh_" + INSTANCE.replace("-", ""),
            "cavesEnabled": False})
        self.write(self.instance / "active-release.json", {"schemaVersion": 1, "dstReleaseId": RELEASE,
            "modsReleaseId": None})
        self.write(self.instance / "compose.yaml", "services: {}\n")
        self.write(self.instance / "desired" / "mods.json", {"schemaVersion": 1, "instanceId": INSTANCE,
            "revision": 3, "mods": [{"workshopId": "123", "enabled": True,
                "configurationOptions": {"enabled": False}, "artifactDigest": CONTENT}]})
        self.write(self.instance / "deployed-mods.json", {"schemaVersion": 1, "instanceId": INSTANCE,
            "revision": 2, "mods": []})
        self.write(self.instance / "config" / "Cluster_1" / "Master" / "server.ini",
            "[NETWORK]\nserver_port=11000\n[STEAM]\nmaster_server_port=27000\n")
        self.write(self.instance / "data" / "Master" / "save.zip", "save")
        artifact = self.root / "mods" / "prepared" / "123" / CONTENT
        (artifact / "content").mkdir(parents=True, mode=0o700)
        self.write(artifact / "manifest.json", {"schemaVersion": 1, "workshopId": "123", "contentDigest": CONTENT})
        self.write(artifact / "content" / "modinfo.lua", "version = '1.0'\n")
        backup = self.root / "backups" / INSTANCE / "old"
        backup.mkdir(mode=0o700)
        self.write(backup / "metadata.json", {"schemaVersion": 1, "backupId": "77777777-7777-4777-8777-777777777777",
            "captureMode": "cold", "verification": "verified", "checksum": "b" * 64})
        self.request = {"schemaVersion": 1, "action": "preview", "requestId": REQUEST,
            "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 3}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        path.chmod(0o600)

    def test_validate_desired_reads_host_level_artifact(self):
        with patch.object(MODULE, "ROOT", self.root):
            desired, mods, _ = MODULE.validate_desired(self.instance, self.request)
        self.assertEqual(mods[0]["artifactDigest"], CONTENT)
        self.assertEqual(desired["instanceId"], INSTANCE)

    def test_preview_describes_add_without_restore_or_backup_gate(self):
        shutil.rmtree(self.root / "backups")
        with patch.object(MODULE, "ROOT", self.root), \
             patch.object(MODULE, "container_roles", return_value=(["master"], "running")):
            result = MODULE.preview(self.request)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["changes"], [{"workshopId": "123", "action": "add"}])
        self.assertEqual(result["blockers"], [])

    def test_preview_covers_disable_and_remove_without_claiming_unrelated_mods(self):
        self.write(self.instance / "deployed-mods.json", {"schemaVersion": 1, "instanceId": INSTANCE,
            "revision": 2, "mods": [
                {"workshopId": "123", "enabled": True, "configurationOptions": {"enabled": False},
                 "artifactDigest": CONTENT},
                {"workshopId": "456", "enabled": True, "configurationOptions": {}, "artifactDigest": "c" * 64},
            ]})
        desired = json.loads((self.instance / "desired" / "mods.json").read_text())
        desired["mods"][0]["enabled"] = False
        self.write(self.instance / "desired" / "mods.json", desired)
        with patch.object(MODULE, "ROOT", self.root), \
             patch.object(MODULE, "container_roles", return_value=(["master"], "stopped")):
            result = MODULE.preview(self.request)
        self.assertEqual(result["changes"], [
            {"workshopId": "123", "action": "disable"},
            {"workshopId": "456", "action": "remove"},
        ])

    def test_start_requires_an_explicit_backup_choice(self):
        request = {**self.request, "action": "start", "operationId": "77777777-7777-4777-8777-777777777777",
                   "desiredDigest": CONTENT}
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.valid_request(request)
        self.assertEqual(failure.exception.code, "MOD_APPLY_INPUT_INVALID")
        MODULE.valid_request({**request, "createProtectionBackup": False})
        MODULE.valid_request({**request, "createProtectionBackup": True})

    def test_start_rejects_a_noop_even_when_there_are_no_other_blockers(self):
        tasks = self.root / "mod-apply-tasks"
        tasks.mkdir(mode=0o700)
        request = {**self.request, "action": "start", "operationId": "77777777-7777-4777-8777-777777777777",
                   "desiredDigest": CONTENT}
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "TASKS", tasks), \
             patch.object(MODULE.os, "geteuid", return_value=0), patch.object(MODULE, "safe_dir"), \
             patch.object(MODULE.os, "open", return_value=91), patch.object(MODULE.os, "close"), \
             patch.object(MODULE.fcntl, "flock"), \
             patch.object(MODULE, "preview", return_value={"desiredDigest": CONTENT, "blockers": [], "allowed": False}):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(request, "worker", "archive", "release", "runtime", "restore", "download")
        self.assertEqual(failure.exception.code, "MOD_APPLY_NO_CHANGES")

    def test_error_payload_reflects_the_backup_choice(self):
        request = {"hostId": HOST, "instanceId": INSTANCE}
        with_backup = MODULE.error_payload("MOD_APPLY_FAILED", request, REQUEST, True, True)
        without_backup = MODULE.error_payload("MOD_APPLY_FAILED", request, REQUEST, True, False)
        self.assertIn("保护备份恢复", with_backup["message"])
        self.assertIn("未创建冷保护备份", without_backup["message"])
        self.assertIn("无法自动恢复", without_backup["recoverySteps"][0])
        rollback = MODULE.error_payload("MOD_APPLY_ROLLBACK_FAILED", request, REQUEST, True, True)
        self.assertIn("自动恢复未完成", rollback["message"])

    def test_lua_rendering_quotes_values_and_rejects_null(self):
        output = MODULE.modoverrides([{"workshopId": "123", "enabled": True,
            "configurationOptions": {"label": 'a"b', "disabled": False}}]).decode()
        self.assertIn('["workshop-123"]', output)
        self.assertIn('["label"]="a\\"b"', output)
        self.assertIn('["disabled"]=false', output)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.modoverrides([{"workshopId": "123", "enabled": True,
                "configurationOptions": {"unsupported": None}}])
        self.assertEqual(failure.exception.code, "MOD_CONFIG_UNSUPPORTED")

    def test_release_verification_rejects_changed_copy_and_setup(self):
        release_root = Path(self.temp.name) / "release"
        content = release_root / "Master" / "steamapps" / "workshop" / "content" / "322330" / "123"
        content.mkdir(parents=True)
        (content / "modinfo.lua").write_text("version = '1.0'\n")
        setup = MODULE.setup_script([{"workshopId": "123", "enabled": True}])
        (release_root / "dedicated_server_mods_setup.lua").write_bytes(setup)
        artifact_digest = hashlib.sha256((content / "modinfo.lua").read_bytes()).hexdigest()
        mods = [{"workshopId": "123", "enabled": True,
                 "configurationOptions": {}, "artifactDigest": artifact_digest}]
        download = {"content_files": lambda path: ([], 0,
            hashlib.sha256((path / "modinfo.lua").read_bytes()).hexdigest())}
        MODULE.verify_mod_release(release_root, mods, ["master"], download)

        (content / "modinfo.lua").write_text("changed")
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.verify_mod_release(release_root, mods, ["master"], download)
        self.assertEqual(failure.exception.code, "MOD_RELEASE_CONTENT_INVALID")

    def test_mod_protection_backup_metadata_resolves_the_dst_build(self):
        catalog = self.root / "releases" / "catalog.json"
        catalog.parent.mkdir(mode=0o700)
        self.write(catalog, {"schemaVersion": 1, "releases": [
            {"releaseId": RELEASE, "verified": True, "dstBuild": "760944"}]})
        backup_id = "99999999-9999-4999-8999-999999999999"
        request = {"hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 3}
        archive = {"create_archive": lambda _instance, path, _identity, _roots:
                       (path.write_bytes(b"archive"), {"archiveSizeBytes": 7, "checksum": "b" * 64})[1],
                   "inspect_archive": lambda *_args: None}
        destination = self.root / "mod-apply-tasks" / REQUEST / "protection" / backup_id
        with patch.object(MODULE, "ROOT", self.root):
            MODULE.backup_current(self.instance, request, ["master"], archive, backup_id, destination)
        metadata = json.loads((destination / "metadata.json").read_text())
        self.assertEqual(metadata["dstBuild"], "760944")
        self.assertEqual(metadata["restorability"], "restorable")
        self.assertEqual(metadata["dstReleaseId"], RELEASE)

    def test_rollback_removes_deployed_snapshot_that_did_not_exist_before_apply(self):
        operation_id = "77777777-7777-4777-8777-777777777777"
        backup_id = "88888888-8888-4888-8888-888888888888"
        snapshot = Path(self.temp.name) / "snapshot"
        for relative in ("config/Cluster_1/Master", "data/Master", "desired"):
            (snapshot / relative).mkdir(parents=True, exist_ok=True)
        (snapshot / "data/Master/save.zip").write_bytes(b"world")
        (snapshot / "config/Cluster_1/Master/modoverrides.lua").write_bytes(b"return {}\n")
        (snapshot / "desired/mods.json").write_text((self.instance / "desired/mods.json").read_text())
        self.write(snapshot / "active-release.json", {"schemaVersion": 1, "dstReleaseId": RELEASE,
            "modsReleaseId": None})
        self.write(snapshot / "manifest.json", json.loads((self.instance / "manifest.json").read_text()))
        self.write(snapshot / "compose.yaml", "services: {}\n")
        token_path = self.instance / "config" / "Cluster_1" / "cluster_token.txt"
        token_path.write_text("current-token\n"); token_path.chmod(0o600)
        admin_path = self.instance / "config" / "Cluster_1" / "adminlist.txt"
        admin_path.write_text("STEAM_1\n"); admin_path.chmod(0o600)
        world_path = self.instance / "desired" / "world.json"
        self.write(world_path, {"schemaVersion": 1, "instanceId": INSTANCE, "revision": 3,
            "settings": {"roomName": "keep"}, "secrets": {"tokenRef": None, "serverPasswordRef": None}})
        world_before = world_path.read_bytes()
        (self.instance / "staging" / "mod-apply" / operation_id).mkdir(parents=True, mode=0o700)
        backup_path = Path(self.temp.name) / "backup"
        backup_path.mkdir()
        self.write(backup_path / "metadata.json", {"checksum": "b" * 64})
        (backup_path / "archive.tar.gz").write_bytes(b"archive")

        def extract(_archive, destination, *_args):
            shutil.copytree(snapshot, destination)

        request = {"operationId": operation_id, "hostId": HOST, "instanceId": INSTANCE}
        MODULE.restore_paths_from_backup(self.instance, {"extract_to_staging": extract},
            {"assign_runtime_ownership": lambda _path: None},
            backup_path, request, backup_id, ["master"])
        self.assertFalse((self.instance / "deployed-mods.json").exists())
        self.assertEqual(token_path.read_text(), "current-token\n")
        self.assertEqual(admin_path.read_text(), "STEAM_1\n")
        self.assertEqual(world_path.read_bytes(), world_before)

    def test_controller_holds_locks_and_runs_worker_with_lease(self):
        root = self.root
        tasks = root / "mod-apply-tasks"
        task_id = "99999999-9999-4999-8999-999999999999"
        task_dir = tasks / task_id
        task_dir.mkdir(parents=True, mode=0o700)
        (root / "instances" / INSTANCE / "locks").mkdir(parents=True, mode=0o700, exist_ok=True)
        (root / "locks").mkdir(mode=0o700, exist_ok=True)
        registry = {"schemaVersion": 1, "hostId": HOST, "registryRevision": 0,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready", "pendingTaskId": task_id}],
            "heavyQueue": [{"taskId": task_id, "operation": "modApply", "instanceId": INSTANCE,
                            "requestedAtUnixSeconds": 1, "ownerToken": "owner", "generation": 1}]}
        (root / "registry.json").write_text(json.dumps(registry)); (root / "registry.json").chmod(0o600)
        request = {"operationId": task_id, "instanceId": INSTANCE}
        (task_dir / "request.json").write_text(json.dumps(request)); (task_dir / "request.json").chmod(0o600)
        status = {"schemaVersion": 1, "taskId": task_id, "operationId": task_id,
            "target": {"serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE},
            "operation": "modApply", "status": "queued", "stage": "queued", "sequence": 1,
            "createdAt": MODULE.now(), "cancelAllowed": True}
        (task_dir / "status.json").write_text(json.dumps(status)); (task_dir / "status.json").chmod(0o600)
        worker = (task_dir / "worker.py")
        worker.write_text(
            "import json, os, pathlib\n"
            "root = pathlib.Path(os.environ['TEST_ROOT'])\n"
            "(root / 'lease.txt').write_text(os.environ.get('FAMINE_CONTROLLER_LEASE', ''))\n"
            "path = root / 'mod-apply-tasks' / '99999999-9999-4999-8999-999999999999' / 'status.json'\n"
            "state = json.loads(path.read_text())\n"
            "state['status'] = 'succeeded'\nstate['sequence'] = 2\n"
            "path.write_text(json.dumps(state))\n")
        worker.chmod(0o700)
        old = os.environ.get("TEST_ROOT")
        os.environ["TEST_ROOT"] = str(root)
        try:
            with patch.object(MODULE, "ROOT", root), patch.object(MODULE, "TASKS", tasks), \
                 patch.object(MODULE, "CONTROLLER_TASKS", {"modApply": tasks}), \
                 patch.object(MODULE.time, "sleep", lambda _seconds: None):
                MODULE.controller_main()
        finally:
            if old is None: os.environ.pop("TEST_ROOT", None)
            else: os.environ["TEST_ROOT"] = old
        self.assertEqual((root / "lease.txt").read_text(), task_id)
        saved = json.loads((root / "registry.json").read_text())
        self.assertEqual(saved["heavyQueue"], [])
        self.assertIsNone(saved["instances"][0]["pendingTaskId"])

    def test_cancel_request_is_written_for_an_active_task(self):
        tasks = self.root / "mod-apply-tasks"
        task_id = "77777777-7777-4777-8777-777777777777"
        directory = tasks / task_id
        directory.mkdir(parents=True, mode=0o700)
        MODULE.atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": task_id,
            "operationId": task_id, "target": {"serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE},
            "operation": "modApply", "status": "queued", "stage": "queued", "sequence": 1,
            "createdAt": MODULE.now(), "cancelAllowed": True})
        request = {"schemaVersion": 1, "action": "cancel", "requestId": REQUEST, "serverId": SERVER,
                   "hostId": HOST, "instanceId": INSTANCE, "operationId": task_id}
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "TASKS", tasks):
            state = MODULE.cancel(request)
        self.assertEqual(state["status"], "queued")
        self.assertTrue((directory / "cancel.request").is_file())

    def test_admission_records_pending_task_and_heavy_queue_then_releases(self):
        request = {**self.request, "operationId": "77777777-7777-4777-8777-777777777777"}
        with patch.object(MODULE, "ROOT", self.root):
            MODULE.admit_queue(request)
            registry = json.loads((self.root / "registry.json").read_text())
            self.assertEqual(registry["instances"][0]["pendingTaskId"], request["operationId"])
            self.assertEqual(registry["heavyQueue"][0]["taskId"], request["operationId"])
            self.assertEqual(registry["heavyQueue"][0]["operation"], "modApply")
            self.assertEqual(MODULE.queue_position(request["operationId"]), 1)
            MODULE.release_queue(request["operationId"])
            registry = json.loads((self.root / "registry.json").read_text())
            self.assertIsNone(registry["instances"][0]["pendingTaskId"])
            self.assertEqual(registry["heavyQueue"], [])
            self.assertIsNone(MODULE.queue_position(request["operationId"]))

    def test_heavy_lock_waits_for_the_current_owner_instead_of_rejecting(self):
        with tempfile.TemporaryDirectory(prefix="mods-lock-") as temporary:
            path = Path(temporary) / "heavy.lock"
            first = MODULE.acquire_lock(path)
            acquired = []
            waiter = threading.Thread(target=lambda: acquired.append(MODULE.acquire_lock(path, wait=True)))
            waiter.start()
            time.sleep(0.05)
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.acquire_lock(path)
            self.assertEqual(failure.exception.code, "MOD_APPLY_BUSY")
            os.close(first)
            waiter.join(timeout=2)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(len(acquired), 1)
            os.close(acquired[0])


if __name__ == "__main__":
    unittest.main()
