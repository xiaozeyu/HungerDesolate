"""Test read-only release preparation preflight."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_prepare", PROJECT / "remote-scripts/probes/instance-release-prepare.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
RELEASE = {"releaseId": "22222222-2222-4222-8222-222222222222", "verified": True}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-prepare-")
        root = Path(self.temp.name) / "famine-hub"
        (root / "releases" / "staging").mkdir(parents=True, mode=0o700)
        (root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (root / "host.json").chmod(0o600)
        self.root = root
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned(info):
            values = list(info); values[4] = 0
            return os.stat_result(values)
        self.patches = [patch.object(MODULE, "ROOT", root), patch.object(MODULE, "CATALOG", root / "releases" / "catalog.json"), patch.object(MODULE, "STAGING", root / "releases" / "staging"), patch.object(MODULE.os, "geteuid", return_value=0), patch.object(Path, "lstat", lambda path: root_owned(original_lstat(path))), patch.object(MODULE.os, "fstat", lambda fd: root_owned(original_fstat(fd)))]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_missing_catalog_is_blocked(self):
        result = MODULE.inspect({"hostId": HOST, "releaseId": RELEASE["releaseId"], "catalogDigest": "sha256:" + "a" * 64})
        self.assertEqual(result["blockers"], ["CREATE_RELEASE_REQUIRED"])

    def test_catalog_digest_change_is_rejected(self):
        catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [RELEASE]}
        MODULE.CATALOG.write_text(json.dumps(catalog)); MODULE.CATALOG.chmod(0o600)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.inspect({"hostId": HOST, "releaseId": RELEASE["releaseId"], "catalogDigest": "sha256:" + "a" * 64})
        self.assertEqual(str(failure.exception), "RELEASE_CATALOG_CHANGED")

    def test_verified_release_requires_artifact_staging(self):
        catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [RELEASE]}
        MODULE.CATALOG.write_text(json.dumps(catalog)); MODULE.CATALOG.chmod(0o600)
        digest = MODULE.catalog_digest(catalog)
        result = MODULE.inspect({"hostId": HOST, "releaseId": RELEASE["releaseId"], "catalogDigest": digest})
        self.assertEqual(result["blockers"], ["CREATE_RELEASE_ARTIFACT_REQUIRED"])
        (MODULE.STAGING / RELEASE["releaseId"]).mkdir(mode=0o700)
        ready = MODULE.inspect({"hostId": HOST, "releaseId": RELEASE["releaseId"], "catalogDigest": digest})
        self.assertEqual(ready["status"], "ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
