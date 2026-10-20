"""Test fixed release validation and Compose rendering."""
import importlib.util
import json
from pathlib import Path
import unittest

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
        self.assertIn(instance_root + "/config:/srv/config:rw", result)
        self.assertNotIn("/srv/dst:ro", result)
        self.assertIn("depends_on:", result)
        self.assertNotIn("token", result.lower())
        self.assertNotIn("password", result.lower())

    def test_disabled_caves_do_not_render_caves_service_or_ports(self):
        result = MODULE.render_compose(manifest(False), release(), "/var/lib/famine-hub/instances/" + INSTANCE_ID)
        self.assertIn("master:", result)
        self.assertNotIn("\n  caves:", result)
        self.assertNotIn("11001:11001/udp", result)

    def test_rejects_absolute_or_traversal_paths(self):
        for path in ("instances/" + INSTANCE_ID,
                     "/var/lib/famine-hub/instances/../" + INSTANCE_ID,
                     "/var/lib/famine-hub/other/" + INSTANCE_ID):
            with self.subTest(path=path), self.assertRaises(MODULE.ReleaseRejected):
                MODULE.render_compose(manifest(), release(), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
