import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mods_save", PROJECT / "remote-scripts/actions/mods-save.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
SERVER = "33333333-3333-4333-8333-333333333333"


class ModsSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mods-save-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.instance = self.root / "instances" / INSTANCE
        for path in (self.root / "instances", self.instance / "locks", self.instance / "desired", self.instance / "operations"):
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.chmod(0o700)
        self.root.chmod(0o700)
        (self.root / "instances").chmod(0o700)
        self.instance.chmod(0o700)
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (self.root / "registry.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "instances": [{"instanceId": INSTANCE, "lifecycle": "ready", "relativePath": "instances/" + INSTANCE}]}))
        for path in (self.root / "host.json", self.root / "registry.json"):
            path.chmod(0o600)
        self.manifest = {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE, "configRevision": 0}
        manifest = self.instance / "manifest.json"
        manifest.write_text(json.dumps(self.manifest)); manifest.chmod(0o600)
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_lstat(path):
            values = list(original_lstat(path)); values[4] = 0; return os.stat_result(values)
        def root_fstat(fd):
            values = list(original_fstat(fd)); values[4] = 0; return os.stat_result(values)
        self.patches = [patch.object(MODULE, "ROOT", self.root), patch.object(MODULE.os, "geteuid", return_value=0),
                        patch.object(Path, "lstat", root_lstat), patch.object(MODULE.os, "fstat", root_fstat)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def request(self, **updates):
        value = {"schemaVersion": 1, "requestId": "44444444-4444-4444-8444-444444444444",
                 "operationId": "55555555-5555-4555-8555-555555555555", "serverId": SERVER,
                 "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 0,
                 "mods": [{"workshopId": "123456", "enabled": True, "configurationOptions": {}, "artifactDigest": None}]}
        value.update(updates)
        return value

    def test_saves_desired_mods_and_replays_lost_response(self):
        request = self.request()
        output = io.StringIO()
        with contextlib.redirect_stdout(output): MODULE.main(request)
        result = json.loads(output.getvalue())
        self.assertEqual(result["verifiedRevision"], 1)
        desired = json.loads((self.instance / "desired" / "mods.json").read_text())
        self.assertEqual(desired["revision"], 1)
        self.assertEqual(desired["mods"][0]["workshopId"], "123456")
        output = io.StringIO()
        with contextlib.redirect_stdout(output): MODULE.main(request)
        self.assertEqual(json.loads(output.getvalue())["verifiedRevision"], 1)

    def test_rejects_stale_revision_without_overwriting_mods(self):
        with contextlib.redirect_stdout(io.StringIO()): MODULE.main(self.request())
        stale = self.request(operationId="66666666-6666-4666-8666-666666666666")
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.main(stale)
        self.assertEqual(str(failure.exception), "REVISION_CONFLICT")

    def test_rejects_duplicate_workshop_ids(self):
        value = self.request(mods=[{"workshopId": "123", "enabled": True, "configurationOptions": {}},
                                   {"workshopId": "123", "enabled": False, "configurationOptions": {}}])
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.main(value)
        self.assertEqual(str(failure.exception), "MOD_DUPLICATE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
