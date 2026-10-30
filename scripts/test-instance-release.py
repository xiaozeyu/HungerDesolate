"""Test fixed release validation and Compose rendering."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instance_release", PROJECT / "remote-scripts/actions/instance-release.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

RELEASE_ID = "11111111-1111-4111-8111-111111111111"
INSTANCE_ID = "22222222-2222-4222-8222-222222222222"


def release():
    return {"schemaVersion": 1, "releaseId": RELEASE_ID, "platform": "linux/amd64",
            "image": "registry.example/dst", "imageDigest": "sha256:" + "a" * 64,
            "dstBuild": "build-2026", "artifactDigest": "sha256:" + "b" * 64,
            "scriptVersion": "scripts-1", "verified": True, "runtimeVerified": False,
            "commands": {"master": ["/opt/dst", "master"], "caves": ["/opt/dst", "caves"]}}


def manifest(caves=True):
    ports = [{"shard": shard, "purpose": purpose, "protocol": "udp", "bindAddress": "0.0.0.0",
              "hostPort": port, "containerPort": port}
             for shard, base in (["master", 11000], ["caves", 11001] if caves else ["master", 11000])
             for purpose, port in zip(("game", "steamAuth", "steamMaster"), (base, 8766 if shard == "master" else 8767, 27016 if shard == "master" else 27017))]
    return {"schemaVersion": 1, "hostId": "33333333-3333-4333-8333-333333333333",
            "instanceId": INSTANCE_ID, "composeProject": "fh_" + INSTANCE_ID.replace("-", ""),
            "cavesEnabled": caves, "autostart": True, "ports": ports}


class ReleaseTests(unittest.TestCase):
    def test_rejects_unverified_or_mutable_release_metadata(self):
        for change in ({"verified": False}, {"platform": "linux/arm64"}, {"imageDigest": "sha256:bad"},
                       {"commands": {"master": ["/opt/dst"], "caves": ["bad\ncommand"]}}):
            value = release()
            value.update(change)
            with self.subTest(change=change), self.assertRaises(MODULE.ReleaseRejected):
                MODULE.validate_release(value)

    def test_renders_digest_pinned_master_and_caves_compose_without_secrets(self):
        instance_root = "/var/lib/famine-hub/instances/" + INSTANCE_ID
        result = MODULE.render_compose(manifest(), release(), instance_root)
        self.assertIn("registry.example/dst@sha256:" + "a" * 64, result)
        self.assertIn("master:", result)
        self.assertIn("caves:", result)
        self.assertIn("11000:11000/udp", result)
        data_mount = instance_root + "/data/Master:/srv/data:rw"
        config_mount = instance_root + "/config:/srv/data/config:rw"
        self.assertIn(data_mount, result)
        self.assertIn(config_mount, result)
        self.assertLess(result.index(data_mount), result.index(config_mount))
        self.assertNotIn("/srv/dst:ro", result)
        self.assertIn("depends_on:", result)
        self.assertIn("working_dir: '/srv/dst/bin64'", result)
        self.assertNotIn("token", result.lower())
        self.assertNotIn("password", result.lower())

    def test_disabled_caves_do_not_render_caves_service_or_ports(self):
        result = MODULE.render_compose(manifest(False), release(), "/var/lib/famine-hub/instances/" + INSTANCE_ID)
        self.assertIn("master:", result)
        self.assertNotIn("\n  caves:", result)
        self.assertNotIn("11001:11001/udp", result)

    def test_mod_release_uses_its_own_ugc_and_setup_file_without_moving_dst_identity(self):
        mod_release_id = "77777777-7777-4777-8777-777777777777"
        instance_root = "/var/lib/famine-hub/instances/" + INSTANCE_ID
        result = MODULE.render_compose({**manifest(), "modsReleaseId": mod_release_id}, release(), instance_root)
        self.assertEqual(result.count("- '-ugc_directory'"), 2)
        self.assertEqual(result.count("- '/srv/ugc'"), 2)
        self.assertIn(instance_root + "/mods/releases/" + mod_release_id + "/Master:/srv/ugc:rw", result)
        self.assertIn(instance_root + "/mods/releases/" + mod_release_id
                      + "/dedicated_server_mods_setup.lua:/srv/dst/mods/dedicated_server_mods_setup.lua:ro", result)
        self.assertIn("releaseId: '" + RELEASE_ID + "'", result)
        self.assertIn("modsReleaseId: '" + mod_release_id + "'", result)

    def test_rejects_absolute_or_traversal_paths(self):
        for path in ("instances/" + INSTANCE_ID,
                     "/var/lib/famine-hub/instances/../" + INSTANCE_ID,
                     "/var/lib/famine-hub/other/" + INSTANCE_ID):
            with self.subTest(path=path), self.assertRaises(MODULE.ReleaseRejected):
                MODULE.render_compose(manifest(), release(), path)

    def test_runtime_paths_change_only_bind_roots_and_preserve_save_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instances" / INSTANCE_ID
            (root / "data" / "Master").mkdir(parents=True, mode=0o700)
            save = root / "data" / "Master" / "save.bin"
            save.write_bytes(b"keep existing world")
            original_fstat = os.fstat
            def root_owned(fd):
                values = list(original_fstat(fd))
                values[4] = 0
                return os.stat_result(values)
            with patch.object(MODULE.os, "fstat", side_effect=root_owned), \
                 patch.object(MODULE.os, "fchown") as chown:
                MODULE.prepare_runtime_paths(str(root), RELEASE_ID, True)
            self.assertEqual(chown.call_count, 4)
            self.assertEqual(save.read_bytes(), b"keep existing world")
            self.assertFalse((root / "data" / "Master" / "save.bin").is_symlink())

    def test_runtime_paths_reject_symlinks_before_changing_owners(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instances" / INSTANCE_ID
            root.mkdir(parents=True)
            external = Path(temporary) / "external"
            external.mkdir()
            (root / "data").symlink_to(external, target_is_directory=True)
            original_fstat = os.fstat
            def root_owned(fd):
                values = list(original_fstat(fd))
                values[4] = 0
                return os.stat_result(values)
            with patch.object(MODULE.os, "fstat", side_effect=root_owned), \
                 patch.object(MODULE.os, "fchown") as chown:
                with self.assertRaises(OSError):
                    MODULE.prepare_runtime_paths(str(root), RELEASE_ID, True)
            chown.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
