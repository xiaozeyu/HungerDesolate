"""Test staged release import validation and catalog publication."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_import", PROJECT / "remote-scripts/actions/instance-release-import.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
RID = "22222222-2222-4222-8222-222222222222"
RELEASE = {"releaseId": RID, "verified": True, "runtimeVerified": False, "image": "registry.example/dst",
           "imageDigest": "sha256:" + "a" * 64, "platform": "linux/amd64", "dstBuild": "build-1",
           "artifactDigest": "sha256:" + "b" * 64, "scriptVersion": "scripts-1",
           "commands": {"master": ["/opt/dst", "master"], "caves": ["/opt/dst", "caves"]}}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-import-")
        root = Path(self.temp.name) / "famine-hub"
        (root / "staging" / RID).mkdir(parents=True, mode=0o700)
        (root / "releases" / "tasks").mkdir(parents=True, mode=0o700)
        for path in [root, root / "staging", root / "releases", root / "releases" / "tasks", root / "host.json"]:
            try: path.chmod(0o700 if path.is_dir() else 0o600)
            except FileNotFoundError: pass
        (root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST})); (root / "host.json").chmod(0o600)
        self.root = root
        original_lstat, original_fstat = Path.lstat, os.fstat
        def owned(info): values=list(info); values[4]=0; return os.stat_result(values)
        self.patches=[patch.object(MODULE,"ROOT",root),patch.object(MODULE,"CATALOG",root/"releases/catalog.json"),patch.object(MODULE,"STAGING",root/"staging"),patch.object(MODULE,"TASKS",root/"releases/tasks"),patch.object(MODULE.os,"geteuid",return_value=0),patch.object(Path,"lstat",lambda path:owned(original_lstat(path))),patch.object(MODULE.os,"fstat",lambda fd:owned(original_fstat(fd)))]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def test_import_requires_runtime_evidence(self):
        catalog={"schemaVersion":1,"hostId":HOST,"releases":[RELEASE]}; MODULE.CATALOG.write_text(json.dumps(catalog)); MODULE.CATALOG.chmod(0o600)
        digest=MODULE.catalog_digest(catalog)
        request={"action":"start","operationId":"33333333-3333-4333-8333-333333333333","releaseId":RID,"hostId":HOST,"catalogDigest":digest}
        result = MODULE.run(request)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "CREATE_RELEASE_RUNTIME_EVIDENCE_REQUIRED")
    def test_import_marks_runtime_verified_after_matching_evidence(self):
        catalog={"schemaVersion":1,"hostId":HOST,"releases":[RELEASE]}; MODULE.CATALOG.write_text(json.dumps(catalog)); MODULE.CATALOG.chmod(0o600)
        artifact=MODULE.STAGING/RID
        (artifact/"release-metadata.json").write_text(json.dumps({"schemaVersion":1,**RELEASE})); (artifact/"release-metadata.json").chmod(0o600)
        evidence={"schemaVersion":1,"releaseId":RID,"imageDigest":RELEASE["imageDigest"],"runtimeVerified":True,"verification":"passed"}
        (artifact/"runtime.json").write_text(json.dumps(evidence)); (artifact/"runtime.json").chmod(0o600)
        digest=MODULE.catalog_digest(catalog); request={"action":"start","operationId":"33333333-3333-4333-8333-333333333333","releaseId":RID,"hostId":HOST,"catalogDigest":digest}
        result=MODULE.run(request)
        self.assertEqual(result["status"],"succeeded")
        self.assertTrue(json.loads(MODULE.CATALOG.read_text())["releases"][0]["runtimeVerified"])


if __name__ == "__main__": unittest.main(verbosity=2)
