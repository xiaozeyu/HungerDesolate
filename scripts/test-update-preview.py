import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_plan", PROJECT / "remote-scripts/actions/update-plan.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "22222222-2222-4222-8222-222222222222"
INSTANCE = "33333333-3333-4333-8333-333333333333"
CURRENT = "44444444-4444-4444-8444-444444444444"
TARGET = "55555555-5555-4555-8555-555555555555"
REQUEST = "66666666-6666-4666-8666-666666666666"


class UpdatePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="update-preview-")
        self.root = Path(self.temp.name) / "hub"
        self.instance = self.root / "instances" / INSTANCE
        (self.instance / "data" / "Master").mkdir(parents=True)
        (self.instance / "data" / "Master" / "save.zip").write_bytes(b"world")
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "registryRevision": 1,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready", "pendingTaskId": None,
                           "dstReleaseId": CURRENT}], "heavyQueue": []})
        self.write(self.instance / "manifest.json", {"schemaVersion": 1, "hostId": HOST,
            "instanceId": INSTANCE, "configRevision": 3,
            "composeProject": "fh_" + INSTANCE.replace("-", ""), "cavesEnabled": False})
        self.write(self.instance / "active-release.json", {"schemaVersion": 1, "dstReleaseId": CURRENT})
        self.write(self.root / "releases" / "catalog.json", {"schemaVersion": 1, "hostId": HOST, "releases": [
            {"releaseId": CURRENT, "dstBuild": "630000", "imageDigest": "sha256:" + "a" * 64,
             "verified": True, "runtimeVerified": True},
            {"releaseId": TARGET, "dstBuild": "639491", "imageDigest": "sha256:" + "b" * 64,
             "verified": True, "runtimeVerified": False},
        ]})
        self.request = {"schemaVersion": 1, "requestId": REQUEST,
                        "target": {"serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE},
                        "expectedRevision": 3, "targetReleaseId": TARGET}
        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "PLANS", self.root / "update-plans"),
            patch.object(MODULE, "runtime_state", return_value=("running", ["master"])),
            patch.object(MODULE, "release_artifacts_valid", return_value=True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_allows_a_newer_verified_release_and_persists_plan(self):
        preview = MODULE.preview(dict(self.request))
        self.assertTrue(preview["allowed"])
        self.assertEqual(preview["current"]["build"], "630000")
        self.assertEqual(preview["targetRelease"]["build"], "639491")
        plan = json.loads((self.root / "update-plans" / (preview["planId"] + ".json")).read_text())
        self.assertEqual(plan["targetReleaseId"], TARGET)
        self.assertEqual(plan["baseRevision"], 3)

    def test_same_release_is_blocked(self):
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.preview(dict(self.request, targetReleaseId=CURRENT))
        self.assertEqual(failure.exception.code, "DST_UPDATE_BLOCKED")
        self.assertIn("UPDATE_SAME_RELEASE", failure.exception.details["blockers"])

    def test_older_build_is_blocked(self):
        self.patches[-1].stop()
        self.patches[-1] = patch.object(MODULE, "release_artifacts_valid", return_value=True)
        self.patches[-1].start()
        catalog = json.loads((self.root / "releases" / "catalog.json").read_text())
        catalog["releases"][1]["dstBuild"] = "620000"
        self.write(self.root / "releases" / "catalog.json", catalog)
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.preview(dict(self.request))
        self.assertIn("UPDATE_TARGET_BUILD_OLDER", failure.exception.details["blockers"])

    def test_revision_conflict_is_rejected(self):
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.preview(dict(self.request, expectedRevision=2))
        self.assertEqual(failure.exception.code, "UPDATE_REVISION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
