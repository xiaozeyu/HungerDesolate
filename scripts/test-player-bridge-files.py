#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "player-bridge-m0.py"
SPEC = importlib.util.spec_from_file_location("player_bridge_m0", HELPER)
M0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M0)
SOURCE = ROOT / "remote-scripts" / "player-bridge"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def state_value(captured_at=None):
    return {
        "schemaVersion": 1,
        "shard": "Master",
        "sequence": 1,
        "capturedAt": captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "players": [
            {"userId": "KU_abc123", "name": "小明", "character": "wilson"},
        ],
    }


class StateValidationTests(unittest.TestCase):
    def test_valid_state_normalizes_players(self):
        value = M0.validate_state(encoded(state_value()))
        self.assertEqual(value["shard"], "Master")
        self.assertEqual(value["players"][0]["name"], "小明")

    def test_file_level_invalid_state_is_rejected(self):
        for mutate in (
            lambda value: value.update(schemaVersion=2),
            lambda value: value.update(shard="master"),
            lambda value: value.update(capturedAt="not-a-time"),
            lambda value: value.update(sequence=-1),
        ):
            value = state_value()
            mutate(value)
            with self.assertRaises(M0.Rejected):
                M0.validate_state(encoded(value))

    def test_oversize_and_invalid_utf8_are_rejected(self):
        with self.assertRaises(M0.Rejected):
            M0.validate_state(b" " * (M0.STATE_LIMIT + 1))
        with self.assertRaises(M0.Rejected):
            M0.validate_state(b'{"schemaVersion":1,"shard":"Master","sequence":1,'
                              b'"capturedAt":"2026-11-17T00:00:00Z","players":[\xff]}')

    def test_diag_is_preserved(self):
        value = state_value()
        value["diag"] = {"commandProcessed": True, "commandReadLen": 12}
        state = M0.validate_state(encoded(value))
        self.assertEqual(state["diag"], {"commandProcessed": True, "commandReadLen": 12})

    def test_bad_entries_are_skipped_with_warning(self):
        value = state_value()
        value["players"] = [
            {"userId": "KU_good", "name": "ok", "character": "wilson"},
            {"userId": "KU_bad", "name": "line\nbreak", "character": "wilson"},
            {"userId": "bad id", "name": "x", "character": "wilson"},
            {"userId": "KU_good", "name": "duplicate", "character": "wilson"},
        ]
        state = M0.validate_state(encoded(value))
        self.assertEqual(len(state["players"]), 1)
        self.assertEqual(state["warnings"], ["PLAYER_ENTRY_INVALID"])

    def test_player_limit_is_file_level(self):
        value = state_value()
        value["players"] = [
            {"userId": "KU_{:03d}".format(index), "name": "p", "character": "wilson"}
            for index in range(M0.MAX_PLAYERS + 1)
        ]
        with self.assertRaises(M0.Rejected):
            M0.validate_state(encoded(value))

    def test_errors_do_not_echo_player_data(self):
        with self.assertRaises(M0.Rejected) as caught:
            M0.player_entry({"userId": "KU_bad", "name": "secret-name\n", "character": "x"})
        self.assertNotIn("secret-name", str(caught.exception))


class FreshnessTests(unittest.TestCase):
    def test_thresholds(self):
        now = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
        stamp = lambda seconds: (now + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(M0.freshness(stamp(-15), now.timestamp()), "fresh")
        self.assertEqual(M0.freshness(stamp(-60), now.timestamp()), "stale")
        self.assertEqual(M0.freshness(stamp(-300), now.timestamp()), "unavailable")
        self.assertEqual(M0.freshness(stamp(60), now.timestamp()), "invalid")
        self.assertEqual(M0.freshness("bad", now.timestamp()), "invalid")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_has_no_temp_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            M0.atomic_write(target, b"payload", mode=0o644)
            self.assertEqual(target.read_bytes(), b"payload")
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["state.json"])

    def test_symlink_and_directory_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            real = directory / "real.json"
            real.write_bytes(b"{}")
            link = directory / "link.json"
            os.symlink(real, link)
            with self.assertRaises(M0.Rejected):
                M0.atomic_write(link, b"payload")
            with self.assertRaises(M0.Rejected):
                M0.read_regular(link, 1024)
            folder = directory / "folder"
            folder.mkdir()
            with self.assertRaises(M0.Rejected):
                M0.atomic_write(folder, b"payload")

    def test_unsafe_modes_and_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "state.json"
            path.write_bytes(b"{}")
            os.chmod(path, 0o666)
            with self.assertRaises(M0.Rejected):
                M0.read_regular(path, 1024)
            os.chmod(path, 0o644)
            hardlink = directory / "hard.json"
            os.link(path, hardlink)
            with self.assertRaises(M0.Rejected):
                M0.read_regular(path, 1024)


class KeyFileTests(unittest.TestCase):
    def test_round_trip_and_bad_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / M0.STATE_KEY
            M0.write_key_file(path, b'{"a":1}')
            self.assertTrue(path.read_bytes().startswith(M0.KLEI_HEADER))
            self.assertEqual(M0.read_key_file(path, 1024), b'{"a":1}')
            path.write_bytes(b"BAD" + b'{"a":1}')
            with self.assertRaises(M0.Rejected):
                M0.read_key_file(path, 1024)


class ComposeInstallationTests(unittest.TestCase):
    def test_mount_insertion_is_idempotent(self):
        instance = "/var/lib/famine-hub/instances/22222222-2222-4222-8222-222222222222"
        compose = (
            "services:\n"
            "  master:\n"
            "    volumes:\n"
            "      - '" + instance + "/data/Master:/srv/data:rw'\n"
            "      - '" + instance + "/config:/srv/data/config:rw'\n"
            "  caves:\n"
            "    volumes:\n"
            "      - '" + instance + "/data/Caves:/srv/data:rw'\n"
        )
        first = M0.insert_bridge_mounts(compose, instance, {"master", "caves"})
        self.assertIn(instance + "/bridge/Master:" + M0.COMPOSE_MOUNT, first)
        self.assertIn(instance + "/bridge/Caves:" + M0.COMPOSE_MOUNT, first)
        second = M0.insert_bridge_mounts(first, instance, {"master", "caves"})
        self.assertEqual(first, second)
        self.assertEqual(compose.count("/srv/dst/mods/hungerhub_bridge"), 0)

    def test_missing_data_mount_is_rejected(self):
        with self.assertRaises(M0.Rejected):
            M0.insert_bridge_mounts("services:\n  master:\n", "/tmp/a", {"master"})


class OverridesTests(unittest.TestCase):
    def test_empty_and_missing_overrides(self):
        text, created = M0.append_bridge_override(None)
        self.assertTrue(created)
        self.assertIn(M0.BRIDGE_OVERRIDE_KEY, text)
        text, created = M0.append_bridge_override("return {\n}\n")
        self.assertTrue(created)
        self.assertEqual(text.count(M0.BRIDGE_OVERRIDE_KEY), 1)

    def test_existing_entries_are_preserved(self):
        original = 'return {\n  ["workshop-123"]={enabled=true,configuration_options={}}\n}\n'
        text, created = M0.append_bridge_override(original)
        self.assertTrue(created)
        self.assertIn(M0.BRIDGE_OVERRIDE_KEY, text)
        self.assertIn('["workshop-123"]', text)
        self.assertTrue(M0.overrides_have_user_mods(original))
        self.assertTrue(M0.overrides_have_user_mods(text))

    def test_already_enabled_and_unknown_formats(self):
        original = 'return {\n  ["hungerhub_bridge"]={enabled=true}\n}\n'
        text, created = M0.append_bridge_override(original)
        self.assertEqual(text, original)
        self.assertFalse(created)
        with self.assertRaises(M0.Rejected):
            M0.append_bridge_override("not lua")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_root = M0.ROOT
        self.old_instances = M0.INSTANCES
        self.root = Path(self.temp.name) / "famine-hub"
        self.instances = self.root / "instances"
        self.instance = self.instances / "22222222-2222-4222-8222-222222222222"
        (self.instance / "config" / "Cluster_1" / "Master" / "save").mkdir(parents=True)
        (self.instance / "config" / "Cluster_1" / "Caves" / "save").mkdir(parents=True)
        M0.ROOT = self.root
        M0.INSTANCES = self.instances
        self.addCleanup(self.restore)
        self.write_manifest()
        self.write_compose()

    def restore(self):
        M0.ROOT = self.old_root
        M0.INSTANCES = self.old_instances

    def write_manifest(self, caves=True):
        manifest = {
            "schemaVersion": 1,
            "hostId": "11111111-1111-4111-8111-111111111111",
            "instanceId": self.instance.name,
            "composeProject": "fh_22222222222242228222222222222222",
            "cavesEnabled": caves,
        }
        (self.instance / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def write_compose(self):
        instance = str(self.instance)
        compose = (
            "services:\n"
            "  master:\n"
            "    volumes:\n"
            "      - '" + instance + "/data/Master:/srv/data:rw'\n"
            "      - '" + instance + "/config:/srv/data/config:rw'\n"
            "  caves:\n"
            "    volumes:\n"
            "      - '" + instance + "/data/Caves:/srv/data:rw'\n"
        )
        (self.instance / "compose.yaml").write_text(compose, encoding="utf-8")

    def overrides_path(self, shard="Master"):
        return self.instance / "config" / "Cluster_1" / shard / "modoverrides.lua"

    def test_install_status_and_uninstall(self):
        marker = M0.install(str(self.instance), str(SOURCE))
        self.assertEqual(marker["bridgeVersion"], M0.BRIDGE_VERSION)
        for shard in ("Master", "Caves"):
            bridge = self.instance / "bridge" / shard
            self.assertTrue((bridge / "modinfo.lua").is_file())
            self.assertTrue((bridge / "modmain.lua").is_file())
            self.assertIn(M0.BRIDGE_OVERRIDE_KEY, self.overrides_path(shard).read_text(encoding="utf-8"))
        self.assertTrue((self.instance / "compose.m0.yaml").is_file())
        self.assertTrue((self.instance / "bridge" / M0.M0_MARKER).is_file())

        state = state_value()
        M0.write_key_file(M0.key_path(self.instance, "Master", M0.STATE_KEY), encoded(state))
        entries = M0.status(str(self.instance))
        self.assertEqual(entries[0]["freshness"], "fresh")
        self.assertEqual(entries[0]["players"], 1)

        M0.uninstall(str(self.instance))
        self.assertFalse((self.instance / "bridge").exists())
        self.assertFalse((self.instance / "compose.m0.yaml").exists())
        self.assertFalse(self.overrides_path("Master").exists())
        self.assertFalse(self.overrides_path("Caves").exists())
        self.assertFalse(M0.key_path(self.instance, "Master", M0.STATE_KEY).exists())

    def test_user_mods_require_explicit_flag_and_restore(self):
        original = 'return {\n  ["workshop-123"]={enabled=true,configuration_options={}}\n}\n'
        self.overrides_path("Master").write_text(original, encoding="utf-8")
        self.overrides_path("Caves").write_text(original, encoding="utf-8")
        with self.assertRaises(M0.Rejected) as caught:
            M0.install(str(self.instance), str(SOURCE))
        self.assertEqual(caught.exception.code, "MODOVERRIDES_EXISTING_MODS")
        self.assertFalse((self.instance / "bridge").exists())

        M0.install(str(self.instance), str(SOURCE), allow_existing_mods=True)
        self.assertIn('["workshop-123"]', self.overrides_path("Master").read_text(encoding="utf-8"))
        M0.uninstall(str(self.instance))
        self.assertEqual(self.overrides_path("Master").read_text(encoding="utf-8"), original)
        self.assertEqual(self.overrides_path("Caves").read_text(encoding="utf-8"), original)

    def test_dry_run_writes_nothing(self):
        result = M0.install(str(self.instance), str(SOURCE), dry_run=True)
        self.assertIn("/srv/dst/mods/hungerhub_bridge", result["compose"])
        self.assertFalse((self.instance / "bridge").exists())
        self.assertFalse((self.instance / "compose.m0.yaml").exists())

    def test_reinstall_requires_uninstall(self):
        M0.install(str(self.instance), str(SOURCE))
        with self.assertRaises(M0.Rejected) as caught:
            M0.install(str(self.instance), str(SOURCE))
        self.assertEqual(caught.exception.code, "M0_ALREADY_INSTALLED")
        M0.uninstall(str(self.instance))
        M0.install(str(self.instance), str(SOURCE))
        M0.uninstall(str(self.instance))

class LuaSourceTests(unittest.TestCase):
    def test_mod_files_have_expected_guards(self):
        modinfo = (SOURCE / "modinfo.lua").read_text(encoding="utf-8")
        modmain = (SOURCE / "modmain.lua").read_text(encoding="utf-8")
        self.assertIn("server_only_mod = true", modinfo)
        self.assertIn("all_clients_require_mod = false", modinfo)
        for marker in ("AllPlayers", "SetPersistentString", "STATE_KEY",
                       "pcall", "utf8_sanitize"):
            self.assertIn(marker, modmain)
        for forbidden in ("io.popen", "os.execute", "loadstring"):
            self.assertNotIn(forbidden, modmain)
        for line in modmain.splitlines():
            if "print(" in line:
                self.assertNotIn("player.name", line)
                self.assertNotIn("userid", line)
                self.assertNotIn("expectedName", line)

    def test_bridge_digest_is_stable_and_source_valid(self):
        M0.validate_bridge_source(SOURCE)
        first = M0.bridge_digest(SOURCE)
        second = M0.bridge_digest(SOURCE)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
