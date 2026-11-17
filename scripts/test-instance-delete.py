"""Test preview-bound, identity-checked deletion of stopped instance resources."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "instance_delete", PROJECT / "remote-scripts/actions/instance-delete.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
TASK = "33333333-3333-4333-8333-333333333333"
OPERATION = "44444444-4444-4444-8444-444444444444"
CONTAINER = "a" * 64
NETWORK = "b" * 64


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-delete-")
        self.root = Path(self.temp.name) / "famine-hub"
        (self.root / "locks").mkdir(parents=True, mode=0o700)
        (self.root / "instances" / INSTANCE).mkdir(parents=True, mode=0o700)
        (self.root / "tasks").mkdir(mode=0o700)
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        entry = {"instanceId": INSTANCE, "lifecycle": "ready", "relativePath": "instances/" + INSTANCE,
                 "displayName": "测试专服", "pendingTaskId": None}
        self.registry = {"schemaVersion": 1, "hostId": HOST, "registryRevision": 7,
                         "instances": [entry],
                         "portReservations": [{"hostId": HOST, "instanceId": INSTANCE, "hostPort": 11000}],
                         "operations": []}
        (self.root / "host.json").chmod(0o600)
        (self.root / "registry.json").write_text(json.dumps(self.registry))
        (self.root / "registry.json").chmod(0o600)
        (self.root / "instances" / INSTANCE / "world.dat").write_bytes(b"preserve")
        (self.root / "instances" / INSTANCE / "world.dat").chmod(0o600)
        original_lstat, original_fstat = Path.lstat, os.fstat

        def root_owned_path(path):
            values = list(original_lstat(path))
            values[4] = 0
            return os.stat_result(values)

        def root_owned_fd(fd):
            values = list(original_fstat(fd))
            values[4] = 0
            return os.stat_result(values)

        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "REGISTRY", self.root / "registry.json"),
            patch.object(MODULE, "LOCK", self.root / "locks" / "registry.lock"),
            patch.object(MODULE, "INSTANCES", self.root / "instances"),
            patch.object(MODULE, "TASKS", self.root / "tasks"),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", root_owned_path),
            patch.object(MODULE.os, "fstat", root_owned_fd),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, action="preview", revision=7):
        return {"schemaVersion": 1, "action": action, "hostId": HOST, "instanceId": INSTANCE,
                "expectedRegistryRevision": revision}

    def resources(self, statuses=("created",)):
        containers = [{"id": CONTAINER, "role": "master", "status": status} for status in statuses]
        networks = [{"id": NETWORK, "name": "fh_test_instance", "endpointIds": [CONTAINER]}]
        return containers, networks

    def mock_inventory(self, container_ids, network_ids, containers, networks):
        self.patches.extend([
            patch.object(MODULE, "docker_ids", side_effect=[container_ids, network_ids]),
            patch.object(MODULE, "inspect_containers", return_value=containers),
            patch.object(MODULE, "inspect_networks", return_value=networks),
        ])
        for item in self.patches[-3:]:
            item.start()

    def test_preview_only_allows_stopped_resources_with_exact_identity(self):
        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        self.assertTrue(plan["allowed"])
        self.assertTrue(plan["dataPresent"])
        self.assertEqual(plan["containers"][0]["status"], "created")
        self.assertEqual(len(plan["networks"]), 1)

    def test_running_containers_are_blocked_without_mutation(self):
        containers, networks = self.resources(("running",))
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        self.assertFalse(plan["allowed"])
        self.assertIn("CONTAINER_RUNNING", plan["blockers"])
        self.assertEqual(json.loads((self.root / "registry.json").read_text())["registryRevision"], 7)

    def test_restarting_container_blocks_deletion_instead_of_force_remove(self):
        containers, networks = self.resources(("restarting",))
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        self.assertFalse(plan["allowed"])
        self.assertIn("CONTAINER_RUNNING", plan["blockers"])

    def test_world_data_can_be_deleted_without_backup(self):
        world = self.root / "instances" / INSTANCE / "data" / "Master"
        world.mkdir(parents=True)
        (world / "server.ini").write_text("world data")
        (world / "server.ini").chmod(0o600)
        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        self.assertTrue(plan["allowed"])
        self.assertTrue(plan["worldData"])
        self.assertNotIn("BACKUP_REQUIRED", plan["blockers"])

    def test_world_data_and_backups_are_permanently_deleted(self):
        world = self.root / "instances" / INSTANCE / "data" / "Master"
        world.mkdir(parents=True)
        (world / "server.ini").write_text("world data")
        (world / "server.ini").chmod(0o600)
        backup_id = "77777777-7777-4777-8777-777777777777"
        backup = self.root / "backups" / INSTANCE / backup_id
        backup.mkdir(parents=True, mode=0o700)
        archive = backup / "archive.tar.gz"
        archive.write_bytes(b"archive")
        archive.chmod(0o600)
        metadata = backup / "metadata.json"
        metadata.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
            "backupId": backup_id, "verification": "verified", "checksum": "a" * 64,
            "createdAt": "2026-11-01T00:00:00Z"}))
        metadata.chmod(0o600)
        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        self.assertTrue(plan["allowed"])
        container_scans = iter(([CONTAINER], [], []))
        network_scans = iter(([NETWORK], [], []))
        with patch.object(MODULE, "docker_ids", side_effect=lambda kind, _id: next(container_scans if kind == "container" else network_scans)), \
             patch.object(MODULE, "inspect_containers", side_effect=lambda ids, *_args: containers if ids else []), \
             patch.object(MODULE, "inspect_networks", side_effect=lambda ids, *_args: networks if ids else []), \
             patch.object(MODULE, "docker_run", return_value=""):
            request = self.request("delete")
            request.update({"planId": plan["planId"], "operationId": OPERATION, "typedName": "测试专服"})
            result = MODULE.locked_operation(request, commit=True)
        self.assertFalse(result["retired"])
        self.assertFalse((self.root / "instances" / INSTANCE).exists())
        self.assertFalse((self.root / "backups" / INSTANCE).exists())
        self.assertFalse((self.root / "retired" / INSTANCE).exists())

    def test_compose_network_without_instance_labels_is_accepted(self):
        project = "fh_" + INSTANCE.replace("-", "")
        row = "\t".join([project + "_instance",
                         json.dumps({"com.docker.compose.project": project,
                                     "com.docker.compose.network": "instance"}),
                         json.dumps({CONTAINER: {}})])
        with patch.object(MODULE, "docker_run", return_value=row + "\n"):
            networks = MODULE.inspect_networks([NETWORK], HOST, INSTANCE, {CONTAINER})
        self.assertEqual(networks[0]["name"], project + "_instance")
        self.assertEqual(networks[0]["endpointIds"], [CONTAINER])

    def test_compose_network_from_another_project_is_rejected(self):
        row = "\t".join(["fh_other_instance",
                         json.dumps({"com.docker.compose.project": "fh_other"}),
                         json.dumps({CONTAINER: {}})])
        with patch.object(MODULE, "docker_run", return_value=row + "\n"):
            with self.assertRaises(MODULE.Rejected):
                MODULE.inspect_networks([NETWORK], HOST, INSTANCE, {CONTAINER})

    def test_deletion_prunes_trash_and_instance_protection_archives(self):
        stale = self.root / "trash" / "99999999-9999-4999-8999-999999999999"
        stale.mkdir(parents=True, mode=0o700)
        (stale / "leftover").write_bytes(b"stale")
        task = self.root / "mod-apply-tasks" / TASK
        protection = task / "protection" / "88888888-8888-4888-8888-888888888888"
        protection.mkdir(parents=True, mode=0o700)
        (protection / "archive.tar.gz").write_bytes(b"mods")
        (task / "status.json").write_text(json.dumps({"schemaVersion": 1,
            "target": {"instanceId": INSTANCE}}))
        other_task = self.root / "mod-apply-tasks" / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        other_protection = other_task / "protection" / "99999999-9999-4999-8999-999999999999"
        other_protection.mkdir(parents=True, mode=0o700)
        (other_protection / "archive.tar.gz").write_bytes(b"other")
        (other_task / "status.json").write_text(json.dumps({"schemaVersion": 1,
            "target": {"instanceId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}}))

        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        plan = MODULE.locked_operation(self.request())
        container_scans = iter(([CONTAINER], [], []))
        network_scans = iter(([NETWORK], [], []))
        with patch.object(MODULE, "docker_ids", side_effect=lambda kind, _id: next(container_scans if kind == "container" else network_scans)), \
             patch.object(MODULE, "inspect_containers", side_effect=lambda ids, *_args: containers if ids else []), \
             patch.object(MODULE, "inspect_networks", side_effect=lambda ids, *_args: networks if ids else []), \
             patch.object(MODULE, "docker_run", return_value=""):
            request = self.request("delete")
            request.update({"planId": plan["planId"], "operationId": OPERATION, "typedName": "测试专服"})
            MODULE.locked_operation(request, commit=True)
        self.assertFalse(protection.exists())
        self.assertTrue((task / "status.json").exists())
        self.assertTrue(other_protection.exists())
        self.assertFalse(stale.exists())

    def test_runtime_owned_private_files_are_safe_to_archive(self):
        data = self.root / "instances" / INSTANCE / "data" / "Master"
        data.mkdir(parents=True)
        save = data / "save.dat"
        save.write_bytes(b"world")
        save.chmod(0o600)
        original_lstat = Path.lstat

        def runtime_owned(path):
            values = list(original_lstat(path))
            if path == save:
                values[4] = 1000
            return os.stat_result(values)

        with patch.object(Path, "lstat", runtime_owned):
            containers, networks = self.resources()
            self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
            plan = MODULE.locked_operation(self.request())
        self.assertTrue(plan["allowed"])
        self.assertNotIn("INSTANCE_DELETE_DATA_UNSAFE", plan["blockers"])

    def test_changed_plan_is_rejected_before_docker_mutation(self):
        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        request = self.request("delete")
        request.update({"planId": "0" * 64, "typedName": "测试专服", "operationId": OPERATION})
        with patch.object(MODULE, "docker_run") as docker_run:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.locked_operation(request, commit=True)
        self.assertEqual(str(failure.exception), "INSTANCE_DELETE_PLAN_STALE")
        docker_run.assert_not_called()

    def test_delete_removes_owned_resources_archives_data_and_is_idempotent(self):
        containers, networks = self.resources()
        self.mock_inventory([CONTAINER], [NETWORK], containers, networks)
        preview = MODULE.locked_operation(self.request())
        self.assertTrue(preview["allowed"])

        container_scans = iter(([CONTAINER], [], []))
        network_scans = iter(([NETWORK], [], []))
        with patch.object(MODULE, "docker_ids", side_effect=lambda kind, _id: next(container_scans if kind == "container" else network_scans)), \
             patch.object(MODULE, "inspect_containers", side_effect=lambda ids, *_args: containers if ids else []), \
             patch.object(MODULE, "inspect_networks", side_effect=lambda ids, *_args: networks if ids else []), \
             patch.object(MODULE, "docker_run", return_value="") as docker_run:
            request = self.request("delete")
            request.update({"planId": preview["planId"], "operationId": OPERATION, "typedName": "测试专服"})
            result = MODULE.locked_operation(request, commit=True)
            self.assertEqual(result["removedContainers"], 1)
            self.assertEqual(result["removedNetworks"], 1)
            self.assertFalse(result["retired"])
            self.assertEqual(docker_run.call_count, 2)
            repeated = MODULE.locked_operation(request, commit=True)
            changed = dict(request, typedName="其他专服")
            with self.assertRaises(MODULE.Rejected) as reused:
                MODULE.locked_operation(changed, commit=True)
            self.assertEqual(str(reused.exception), "OPERATION_ID_REUSED")

        registry = json.loads((self.root / "registry.json").read_text())
        self.assertEqual(repeated, result)
        self.assertEqual(registry["registryRevision"], 8)
        self.assertEqual(registry["instances"], [])
        self.assertEqual(registry["portReservations"], [])
        self.assertFalse((self.root / "instances" / INSTANCE).exists())
        self.assertFalse((self.root / "retired" / INSTANCE).exists())
        self.assertRegex(registry["operations"][-1]["payloadDigest"], r"^sha256:[0-9a-f]{64}$")

        # Updating the client must still replay a legacy receipt without deleting anything again.
        registry["operations"][-1]["payloadDigest"] = registry["operations"][-1]["payloadDigest"].removeprefix("sha256:")
        registry_path = self.root / "registry.json"
        registry_path.write_text(json.dumps(registry))
        before = registry_path.read_bytes()
        with patch.object(MODULE, "docker_run") as docker_run:
            legacy_replay = MODULE.locked_operation(request, commit=True)
            self.assertEqual(legacy_replay, result)
            with self.assertRaises(MODULE.Rejected) as reused:
                MODULE.locked_operation(changed, commit=True)
            self.assertEqual(str(reused.exception), "OPERATION_ID_REUSED")
        docker_run.assert_not_called()
        self.assertEqual(registry_path.read_bytes(), before)

    def test_registry_revision_and_name_are_revalidated(self):
        with self.assertRaises(MODULE.Rejected) as stale:
            MODULE.locked_operation(self.request(revision=6))
        self.assertEqual(str(stale.exception), "REGISTRY_CHANGED")
        request = self.request()
        request["typedName"] = "wrong"
        with self.assertRaises(MODULE.Rejected) as name:
            MODULE.locked_operation(request)
        self.assertEqual(str(name.exception), "INSTANCE_CONFIRMATION_MISMATCH")

    def test_tauri_embedded_invocation_emits_response_instead_of_reading_empty_stdin(self):
        source = (PROJECT / "remote-scripts/actions/instance-delete.py").read_text()
        invocation = "import base64\nmain(json.loads(base64.b64decode('" + \
            base64.b64encode(json.dumps({"action": "invalid"}).encode()).decode() + \
            "').decode('utf-8')))\n"
        process = subprocess.run([sys.executable, "-I", "-"], input=source + "\n" + invocation,
                                 capture_output=True, text=True, check=False)
        self.assertIn('"ok":false', process.stdout)
        self.assertIn('"code":"INSTANCE_DELETE_ACTION_INVALID"', process.stdout)
        self.assertNotIn("JSONDecodeError", process.stderr)

    def test_unexpected_filesystem_failure_returns_only_safe_diagnostics(self):
        output = StringIO()
        with patch.object(MODULE, "locked_operation", side_effect=PermissionError(13, "private path")), \
             redirect_stdout(output):
            with self.assertRaises(SystemExit):
                MODULE.main({"schemaVersion": 1, "action": "preview"})
        envelope = json.loads(output.getvalue())
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["code"], "INSTANCE_DELETE_FAILED")
        self.assertEqual(envelope["details"]["exception"], "PermissionError")
        self.assertEqual(envelope["details"]["errno"], 13)
        self.assertNotIn("private path", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
