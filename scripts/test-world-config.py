"""Test instance-scoped desired world configuration persistence and secret isolation."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "world_config", PROJECT / "remote-scripts/actions/world-config.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MODULE.OPTION_SCHEMA = json.loads((PROJECT / "src/lib/dst-world-options.json").read_text())
RENDER_SPEC = importlib.util.spec_from_file_location(
    "world_config_render", PROJECT / "remote-scripts/actions/world-config-render.py"
)
RENDERER = importlib.util.module_from_spec(RENDER_SPEC)
RENDER_SPEC.loader.exec_module(RENDERER)
MODULE.render_config = RENDERER.render
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
SERVER = "33333333-3333-4333-8333-333333333333"
REQUEST = "44444444-4444-4444-8444-444444444444"
OPERATION = "55555555-5555-4555-8555-555555555555"


class WorldConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-config-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.instance = self.root / "instances" / INSTANCE
        (self.root / "locks").mkdir(parents=True, mode=0o700)
        (self.instance / "locks").mkdir(parents=True, mode=0o700)
        (self.instance / "desired").mkdir(mode=0o700)
        (self.instance / "desired-secrets").mkdir(mode=0o700)
        (self.instance / "operations").mkdir(mode=0o700)
        (self.instance / "tasks").mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.instance.chmod(0o700)
        self.manifest = {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
                         "displayName": "生存一服", "configRevision": 0,
                         "composeProject": "fh_22222222222242228222222222222222",
                         "cavesEnabled": True, "autostart": True,
                         "ports": [{"shard": shard, "purpose": purpose, "containerPort": port}
                                   for shard, offset in (("master", 0), ("caves", 1))
                                   for purpose, port in (("game", 11000 + offset),
                                                         ("steamAuth", 8766 + offset),
                                                         ("steamMaster", 27016 + offset))]}
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (self.root / "registry.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "registryRevision": 1, "instances": [{"instanceId": INSTANCE, "lifecycle": "ready"}]}))
        (self.instance / "manifest.json").write_text(json.dumps(self.manifest))
        for path in [self.root / "host.json", self.root / "registry.json", self.instance / "manifest.json"]:
            path.chmod(0o600)
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

    def use_verified_release(self, release_id):
        (self.root / "releases").mkdir(mode=0o700, exist_ok=True)
        path = self.root / "releases/catalog.json"
        path.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "releases": [{"releaseId": release_id, "verified": True, "runtimeVerified": False}]}))
        path.chmod(0o600)

    def settings(self):
        return MODULE.default_settings(True, "生存一服")

    def request(self, action="get", **updates):
        request = {"schemaVersion": 1, "action": action, "requestId": REQUEST,
                   "operationId": OPERATION, "serverId": SERVER, "hostId": HOST,
                   "instanceId": INSTANCE, "expectedRevision": 0,
                   "settings": self.settings(), "tokenChange": None, "serverPasswordChange": None}
        request.update(updates)
        return request

    def test_runtime_config_directory_accepts_uid_1000(self):
        config = self.instance / "config"
        config.mkdir(mode=0o700)
        current_lstat = Path.lstat

        def runtime_owned(path):
            info = current_lstat(path)
            values = list(info)
            values[4] = 1000 if path == config else 0
            return os.stat_result(values)

        with patch.object(Path, "lstat", runtime_owned):
            MODULE.safe_directory(config, allow_runtime=True)
            with self.assertRaises(MODULE.Rejected):
                MODULE.safe_directory(config)

    def test_directory_allows_group_read_but_rejects_group_write(self):
        self.instance.chmod(0o755)
        self.assertEqual(MODULE.get_config(self.request())["data"]["revision"], 0)
        self.instance.chmod(0o775)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.get_config(self.request())
        self.assertEqual(str(failure.exception), "WORLD_CONFIG_PATH_UNSAFE")

    def test_get_returns_defaults_without_secret_material(self):
        result = MODULE.get_config(self.request())
        self.assertEqual(result["data"]["revision"], 0)
        self.assertEqual(result["data"]["desired"]["roomName"], "生存一服")
        self.assertEqual(result["data"]["tokenStatus"], "missing")
        self.assertIsNone(result["data"]["deployedRevision"])

    def test_world_domain_revision_can_lag_global_revision_after_mod_save(self):
        self.manifest["configRevision"] = 1
        (self.instance / "manifest.json").write_text(json.dumps(self.manifest))
        (self.instance / "manifest.json").chmod(0o600)
        (self.instance / "desired" / "mods.json").write_text(json.dumps({
            "schemaVersion": 1, "instanceId": INSTANCE, "revision": 1,
            "mods": [{"workshopId": "123", "enabled": True, "configurationOptions": {}}],
        }))
        (self.instance / "desired" / "mods.json").chmod(0o600)
        result = MODULE.get_config(self.request())
        self.assertEqual(result["configRevision"], 1)
        self.assertEqual(result["data"]["revision"], 0)
        saved = MODULE.save_config(self.request("save", expectedRevision=1))
        self.assertEqual(saved["verifiedRevision"], 2)
        self.assertEqual(saved["data"]["revision"], 2)
        serialized = json.dumps(result).lower()
        self.assertNotIn("cluster_token", serialized)
        self.assertNotIn("server_password", serialized)

    def test_save_uses_cas_and_keeps_secrets_out_of_desired_and_receipt(self):
        settings = self.settings()
        settings["worldgen"]["master"]["worldgenOverrides"]["grass"] = "often"
        result = MODULE.save_config(self.request("save", settings=settings,
            tokenChange={"action": "replace", "value": "secret-token"},
            serverPasswordChange={"action": "replace", "value": "secret-password"}))
        self.assertEqual(result["verifiedRevision"], 1)
        desired = json.loads((self.instance / "desired/world.json").read_text())
        receipt = json.loads((self.instance / "operations" / (OPERATION + ".json")).read_text())
        manifest = json.loads((self.instance / "manifest.json").read_text())
        self.assertEqual(manifest["configRevision"], 1)
        self.assertNotIn("secret-token", json.dumps(desired) + json.dumps(receipt))
        self.assertNotIn("secret-password", json.dumps(desired) + json.dumps(receipt))
        self.assertEqual(len(list((self.instance / "desired-secrets").iterdir())), 2)
        self.assertEqual(MODULE.get_config(self.request())["data"]["tokenStatus"], "configured")

    def test_reveal_returns_secrets_only_on_explicit_request(self):
        MODULE.save_config(self.request("save",
            tokenChange={"action": "replace", "value": "secret-token"},
            serverPasswordChange={"action": "replace", "value": "secret-password"}))
        public = json.dumps(MODULE.get_config(self.request()))
        self.assertNotIn("secret-token", public)
        self.assertNotIn("secret-password", public)
        revealed = MODULE.reveal_secrets(self.request("reveal"))
        self.assertEqual(revealed["token"], "secret-token")
        self.assertEqual(revealed["serverPassword"], "secret-password")

    def test_revision_conflict_and_invalid_option_do_not_write(self):
        stale = self.request("save", expectedRevision=2)
        with self.assertRaises(MODULE.Rejected) as error:
            MODULE.save_config(stale)
        self.assertEqual(str(error.exception), "REVISION_CONFLICT")
        invalid_settings = self.settings()
        invalid_settings["worldgen"]["master"]["worldgenOverrides"]["not_a_dst_option"] = "always"
        with self.assertRaises(MODULE.Rejected) as error:
            MODULE.save_config(self.request("save", settings=invalid_settings))
        self.assertEqual(str(error.exception), "WORLD_OPTION_INVALID")
        self.assertFalse((self.instance / "desired/world.json").exists())

    def test_operation_replay_is_idempotent_and_rejects_changed_secret(self):
        request = self.request("save", tokenChange={"action": "replace", "value": "secret-token"})
        first = MODULE.save_config(request)
        replay = MODULE.save_config(request)
        self.assertEqual(replay["verifiedRevision"], first["verifiedRevision"])
        self.assertEqual(json.loads((self.instance / "manifest.json").read_text())["configRevision"], 1)
        changed = dict(request, tokenChange={"action": "replace", "value": "another-token"})
        with self.assertRaises(MODULE.Rejected) as error:
            MODULE.save_config(changed)
        self.assertEqual(str(error.exception), "OPERATION_ID_REUSED")

    def test_settings_cannot_change_container_topology(self):
        settings = self.settings()
        settings["cavesEnabled"] = False
        with self.assertRaises(MODULE.Rejected) as error:
            MODULE.save_config(self.request("save", settings=settings))
        self.assertEqual(str(error.exception), "WORLD_CONFIG_INVALID")

    def test_apply_preview_reports_world_files_backup_and_new_world_rules(self):
        world = self.instance / "data" / "Master"
        world.mkdir(parents=True, mode=0o755)
        (world / "leveldataoverride.lua").write_text("world")
        (world / "leveldataoverride.lua").chmod(0o644)
        settings = self.settings()
        settings["worldgen"]["master"]["worldgenOverrides"]["grass"] = "often"
        MODULE.save_config(self.request("save", settings=settings,
            tokenChange={"action": "replace", "value": "secret-token"}))
        original_lstat = Path.lstat

        def runtime_owned_path(path):
            values = list(original_lstat(path))
            if path == self.instance / "data" or self.instance / "data" in path.parents:
                values[4] = 1000
            return os.stat_result(values)

        with patch.object(Path, "lstat", runtime_owned_path):
            preview = MODULE.preview_apply({"schemaVersion": 1, "requestId": REQUEST,
                "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
                "expectedRecordVersion": 1, "expectedRevision": 1})
        self.assertFalse(preview["allowed"])
        self.assertTrue(preview["worldDataPresent"])
        self.assertEqual(preview["backupRequirement"], "none")
        self.assertNotIn("PROTECTION_BACKUP_REQUIRED", preview["blockers"])
        self.assertNotIn("WORLD_APPLY_NOT_IMPLEMENTED", preview["blockers"])
        self.assertNotIn("WORLD_RUNTIME_NOT_VERIFIED", preview["blockers"])
        self.assertNotIn("WORLD_CONFIG_RENDER_UNSUPPORTED", preview["blockers"])
        self.assertEqual(preview["newWorldOptions"][0]["key"], "grass")

    def test_apply_preview_blocks_settings_that_cannot_be_rendered(self):
        with patch.object(MODULE, "render_config", side_effect=ValueError("unsupported")):
            preview = MODULE.preview_apply({"schemaVersion": 1, "requestId": REQUEST,
                "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
                "expectedRecordVersion": 1, "expectedRevision": 0})
        self.assertIn("WORLD_CONFIG_RENDER_UNSUPPORTED", preview["blockers"])
        self.assertFalse(preview["allowed"])

    def test_apply_publishes_first_world_config_and_keeps_runtime_unverified(self):
        release_id = "77777777-7777-4777-8777-777777777777"
        self.use_verified_release(release_id)
        (self.instance / "active-release.json").write_text(json.dumps({
            "schemaVersion": 1, "deploymentId": "deployment-1", "dstReleaseId": release_id}))
        (self.instance / "active-release.json").chmod(0o600)
        (self.instance / "compose.yaml").write_text("services: {}\n")
        (self.instance / "compose.yaml").chmod(0o600)
        settings = self.settings()
        MODULE.save_config(self.request("save", settings=settings,
            tokenChange={"action": "replace", "value": "secret-token"}))
        preview = MODULE.preview_apply({"schemaVersion": 1, "requestId": REQUEST,
            "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
            "expectedRecordVersion": 1, "expectedRevision": 1})
        self.assertTrue(preview["allowed"])
        actions = []

        def compose(_instance, _project, *arguments, capture=False, **_kwargs):
            actions.append(arguments)
            return "master\n" if capture else ""

        with patch.object(MODULE, "compose_command", side_effect=compose), \
             patch.object(MODULE.os, "chown", return_value=None):
            result = MODULE.apply_config({"schemaVersion": 1, "action": "apply", "requestId": REQUEST,
                "operationId": "88888888-8888-4888-8888-888888888888", "serverId": SERVER,
                "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 1,
                "desiredDigest": MODULE.digest(settings), "activeReleaseId": release_id})

        self.assertFalse(result["runtimeVerified"])
        self.assertEqual(result["activeReleaseId"], release_id)
        self.assertEqual(result["runningShards"], ["master"])
        self.assertEqual(actions, [
            ("ps", "--services", "--status", "running"), ("restart", "master"),
        ])
        deployed = json.loads((self.instance / "deployed-world.json").read_text())
        self.assertEqual(deployed["digest"], MODULE.digest(settings))
        refreshed = MODULE.get_config(self.request(expectedRevision=1))
        self.assertEqual(refreshed["data"]["deployedRevision"], 1)
        self.assertTrue((self.instance / "config/Cluster_1/cluster.ini").is_file())
        self.assertTrue((self.instance / "config/Cluster_1/adminlist.txt").is_file())
        self.assertNotIn("secret-token", (self.instance / "deployed-world.json").read_text())
        replay = MODULE.apply_config({"schemaVersion": 1, "action": "apply", "requestId": "99999999-9999-4999-8999-999999999999",
            "operationId": "88888888-8888-4888-8888-888888888888", "serverId": SERVER,
            "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 1,
            "desiredDigest": MODULE.digest(settings), "activeReleaseId": release_id})
        self.assertEqual(replay["requestId"], "99999999-9999-4999-8999-999999999999")
        secret_only_update = MODULE.save_config(self.request("save", expectedRevision=1,
            operationId="99999999-9999-4999-8999-999999999999",
            settings=settings, tokenChange={"action": "replace", "value": "replacement-token"}))
        self.assertEqual(secret_only_update["data"]["revision"], 2)
        self.assertEqual(secret_only_update["data"]["deployedRevision"], 1)

        config_dir = self.instance / "config"
        inode_before = config_dir.stat().st_ino
        admin_settings = self.settings()
        admin_settings["advanced"]["adminList"] = ["76561198000000000"]
        MODULE.save_config(self.request("save", expectedRevision=2, settings=admin_settings, operationId="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
        actions.clear()
        with patch.object(MODULE, "compose_command", side_effect=compose), \
             patch.object(MODULE.os, "chown", return_value=None):
            admin_result = MODULE.apply_config({"schemaVersion": 1, "action": "apply", "requestId": REQUEST,
                "operationId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "serverId": SERVER,
                "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 3,
                "desiredDigest": MODULE.digest(admin_settings), "activeReleaseId": release_id})
        self.assertEqual(admin_result["runningShards"], ["master"])
        self.assertEqual(actions, [
            ("ps", "--services", "--status", "running"), ("restart", "master"),
        ])
        self.assertEqual(config_dir.stat().st_ino, inode_before)
        self.assertEqual((config_dir / "Cluster_1/adminlist.txt").read_text(), "76561198000000000\n")

    def test_running_service_response_normalizes_case_and_whitespace(self):
        self.assertEqual(MODULE.parse_running_services("\n Master \r\nCaves\n\n"), {"master", "caves"})
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.parse_running_services("master\nunknown-service\n")
        self.assertEqual(str(failure.exception), "WORLD_DOCKER_RESPONSE_INVALID")
        self.assertEqual(failure.exception.details, {"failureKey": "unknown-service"})

    def test_apply_rolls_back_config_when_service_restart_fails(self):
        release_id = "77777777-7777-4777-8777-777777777777"
        self.use_verified_release(release_id)
        (self.instance / "active-release.json").write_text(json.dumps({
            "schemaVersion": 1, "deploymentId": "deployment-1", "dstReleaseId": release_id}))
        (self.instance / "active-release.json").chmod(0o600)
        (self.instance / "compose.yaml").write_text("services: {}\n")
        (self.instance / "compose.yaml").chmod(0o600)
        old_file = self.instance / "config/Cluster_1/previous.txt"
        old_file.parent.mkdir(parents=True, mode=0o700)
        old_file.write_text("old-config")
        old_file.chmod(0o600)
        settings = self.settings()
        MODULE.save_config(self.request("save", settings=settings,
            tokenChange={"action": "replace", "value": "secret-token"}))
        calls = []

        def compose(_instance, _project, *arguments, capture=False, **_kwargs):
            calls.append(arguments)
            if capture:
                return "master\n"
            if arguments[:1] == ("restart",) and calls.count(arguments) == 1:
                raise MODULE.Rejected("WORLD_DOCKER_OPERATION_FAILED")
            return ""

        with patch.object(MODULE, "compose_command", side_effect=compose), \
             patch.object(MODULE.os, "chown", return_value=None):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.apply_config({"schemaVersion": 1, "action": "apply", "requestId": REQUEST,
                    "operationId": "88888888-8888-4888-8888-888888888888", "serverId": SERVER,
                    "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 1,
                    "desiredDigest": MODULE.digest(settings), "activeReleaseId": release_id})
        self.assertEqual(str(failure.exception), "WORLD_DOCKER_OPERATION_FAILED")
        self.assertEqual(old_file.read_text(), "old-config")
        self.assertFalse((self.instance / "deployed-world.json").exists())
        self.assertEqual(calls[-2:], [("restart", "master"), ("restart", "master")])

    def test_apply_allows_existing_world_files_when_only_config_is_replaced(self):
        release_id = "77777777-7777-4777-8777-777777777777"
        self.use_verified_release(release_id)
        (self.instance / "active-release.json").write_text(json.dumps({
            "schemaVersion": 1, "deploymentId": "deployment-1", "dstReleaseId": release_id}))
        (self.instance / "active-release.json").chmod(0o600)
        (self.instance / "compose.yaml").write_text("services: {}\n")
        (self.instance / "compose.yaml").chmod(0o600)
        world = self.instance / "data/Master/leveldataoverride.lua"
        world.parent.mkdir(parents=True, mode=0o700)
        world.write_text("existing-world")
        world.chmod(0o600)
        settings = self.settings()
        MODULE.save_config(self.request("save", settings=settings,
            tokenChange={"action": "replace", "value": "secret-token"}))
        with patch.object(MODULE, "compose_command", return_value="master\n"), \
             patch.object(MODULE.os, "chown", return_value=None):
            result = MODULE.apply_config({"schemaVersion": 1, "action": "apply", "requestId": REQUEST,
                "operationId": "88888888-8888-4888-8888-888888888888", "serverId": SERVER,
                "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 1,
                "desiredDigest": MODULE.digest(settings), "activeReleaseId": release_id})
        self.assertFalse(result["runtimeVerified"])
        self.assertEqual(world.read_text(), "existing-world")

    def test_apply_preview_classifies_inaccessible_world_data(self):
        (self.instance / "data" / "Master").mkdir(parents=True, mode=0o755)
        with patch.object(Path, "rglob", side_effect=PermissionError):
            with self.assertRaises(MODULE.Rejected) as error:
                MODULE.preview_apply({"schemaVersion": 1, "requestId": REQUEST,
                    "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
                    "expectedRecordVersion": 1, "expectedRevision": 0})
        self.assertEqual(str(error.exception), "WORLD_CONFIG_PATH_UNSAFE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
