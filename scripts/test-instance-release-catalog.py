"""Test read-only DST release catalog validation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_catalog", PROJECT / "remote-scripts/probes/instance-release-catalog.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
RELEASE = {"releaseId": "22222222-2222-4222-8222-222222222222", "platform": "linux/amd64",
           "image": "registry.example/dst", "imageDigest": "sha256:" + "a" * 64,
           "dstBuild": "build-1", "artifactDigest": "sha256:" + "b" * 64,
           "scriptVersion": "scripts-1", "verified": True, "runtimeVerified": True,
           "commands": {"master": ["/opt/dst", "master"], "caves": ["/opt/dst", "caves"]}}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-catalog-")
        root = Path(self.temp.name) / "famine-hub"
        (root / "releases").mkdir(parents=True, mode=0o700)
        (root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (root / "host.json").chmod(0o600)
        self.root = root
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned(path):
            info = original_lstat(path)
            values = list(info); values[4] = 0
            return os.stat_result(values)
        def root_fstat(fd):
            info = original_fstat(fd)
            values = list(info); values[4] = 0
            return os.stat_result(values)
        self.patches = [patch.object(MODULE, "ROOT", root), patch.object(MODULE, "CATALOG", root / "releases" / "catalog.json"), patch.object(MODULE.os, "geteuid", return_value=0), patch.object(Path, "lstat", root_owned), patch.object(MODULE.os, "fstat", root_fstat)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_missing_catalog_is_an_explicit_release_blocker(self):
        result = MODULE.inspect({"hostId": HOST})
        self.assertEqual(result["releases"], [])
        self.assertEqual(result["blockers"], ["CREATE_RELEASE_REQUIRED"])

    def test_verified_catalog_is_read_only_and_digest_bound(self):
        catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [RELEASE]}
        MODULE.CATALOG.write_text(json.dumps(catalog))
        MODULE.CATALOG.chmod(0o600)
        before = MODULE.CATALOG.read_bytes()
        result = MODULE.inspect({"hostId": HOST})
        self.assertEqual(result["releases"][0]["releaseId"], RELEASE["releaseId"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(MODULE.CATALOG.read_bytes(), before)

    def test_unverified_release_does_not_clear_blocker(self):
        release = dict(RELEASE, runtimeVerified=False)
        MODULE.CATALOG.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST, "releases": [release]}))
        MODULE.CATALOG.chmod(0o600)
        self.assertEqual(MODULE.inspect({"hostId": HOST})["blockers"], ["CREATE_RELEASE_REQUIRED"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
