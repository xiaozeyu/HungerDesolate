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

    def settings(self):
        return MODULE.default_settings(True, "生存一服")

    def request(self, action="get", **updates):
        request = {"schemaVersion": 1, "action": action, "requestId": REQUEST,
                   "operationId": OPERATION, "serverId": SERVER, "hostId": HOST,
                   "instanceId": INSTANCE, "expectedRevision": 0,
                   "settings": self.settings(), "tokenChange": None, "serverPasswordChange": None}
        request.update(updates)
        return request

    def test_get_returns_defaults_without_secret_material(self):
        result = MODULE.get_config(self.request())
        self.assertEqual(result["data"]["revision"], 0)
        self.assertEqual(result["data"]["desired"]["roomName"], "生存一服")
        self.assertEqual(result["data"]["tokenStatus"], "missing")
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
        self.assertEqual(preview["backupRequirement"], "required")
        self.assertIn("PROTECTION_BACKUP_REQUIRED", preview["blockers"])
        self.assertIn("WORLD_APPLY_NOT_IMPLEMENTED", preview["blockers"])
        self.assertNotIn("WORLD_CONFIG_RENDER_UNSUPPORTED", preview["blockers"])
        self.assertEqual(preview["newWorldOptions"][0]["key"], "grass")

    def test_apply_preview_blocks_settings_that_cannot_be_rendered(self):
        with patch.object(MODULE, "render_config", side_effect=ValueError("unsupported")):
            preview = MODULE.preview_apply({"schemaVersion": 1, "requestId": REQUEST,
                "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
                "expectedRecordVersion": 1, "expectedRevision": 0})
        self.assertIn("WORLD_CONFIG_RENDER_UNSUPPORTED", preview["blockers"])
        self.assertFalse(preview["allowed"])

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
