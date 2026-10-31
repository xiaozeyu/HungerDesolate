import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_cache", PROJECT / "remote-scripts/probes/build-cache.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"


class BuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-cache-")
        self.root = Path(self.temp.name) / "hub"
        self.build = self.root / "image-build"
        self.context = self.build / "dst-runtime-build"
        self.context.mkdir(parents=True)
        (self.context / "Dockerfile").write_text("FROM scratch\n")
        (self.context / "install.sh").write_text("#!/bin/sh\n")
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.request = {"schemaVersion": 1, "requestId": REQUEST, "serverId": SERVER, "hostId": HOST,
                        "expectedRecordVersion": 1, "action": "preview"}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def docker(self, arguments, timeout=300):
        if arguments[:2] == ["system", "df"]:
            return json.dumps({"Type": "Build Cache", "Size": "1.2GB", "Reclaimable": "900MB"}) + "\n"
        if arguments[:2] == ["builder", "prune"]:
            return "Total:\t0B\n"
        raise AssertionError(arguments)

    def test_previews_context_and_docker_cache(self):
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "BUILD_DIR", self.build), \
             patch.object(MODULE, "CONTEXT", self.context), patch.object(MODULE, "LOCK", self.build / "build.lock"), \
             patch.object(MODULE, "docker", side_effect=self.docker):
            data = MODULE.preview(self.request)
        self.assertGreater(data["contextBytes"], 0)
        self.assertEqual(data["dockerCache"]["size"], "1.2GB")
        self.assertTrue(data["allowed"])

    def test_cleanup_removes_context_and_prunes_builder(self):
        self.request["action"] = "cleanup"
        with patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "BUILD_DIR", self.build), \
             patch.object(MODULE, "CONTEXT", self.context), patch.object(MODULE, "LOCK", self.build / "build.lock"), \
             patch.object(MODULE, "docker", side_effect=self.docker):
            data = MODULE.cleanup(self.request)
        self.assertFalse(self.context.exists())
        self.assertGreater(data["contextBytes"], 0)
        self.assertIn("Total", data["dockerOutput"])


if __name__ == "__main__":
    unittest.main()
