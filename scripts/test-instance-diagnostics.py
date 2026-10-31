import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instance_diagnostics", PROJECT / "remote-scripts/probes/instance-diagnostics.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "22222222-2222-4222-8222-222222222222"
INSTANCE = "33333333-3333-4333-8333-333333333333"
TASK = "44444444-4444-4444-8444-444444444444"
RELEASE = "55555555-5555-4555-8555-555555555555"


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="diagnostics-")
        self.root = Path(self.temp.name) / "hub"
        self.instance = self.root / "instances" / INSTANCE
        (self.instance / "desired").mkdir(parents=True)
        (self.root / "releases").mkdir()
        (self.root / "mod-apply-tasks" / TASK).mkdir(parents=True)
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "instances": [
            {"instanceId": INSTANCE, "lifecycle": "ready", "relativePath": "instances/" + INSTANCE}]})
        self.write(self.instance / "manifest.json", {"schemaVersion": 1, "hostId": HOST,
            "instanceId": INSTANCE, "configRevision": 4, "cavesEnabled": False})
        self.write(self.instance / "active-release.json", {"dstReleaseId": RELEASE, "modsReleaseId": None})
        self.write(self.instance / "desired" / "world.json", {"revision": 4})
        self.write(self.instance / "desired" / "mods.json", {"revision": 4})
        self.write(self.root / "releases" / "catalog.json", {"releases": [{"releaseId": RELEASE,
            "verified": True, "dstBuild": "760944"}]})
        self.write(self.root / "mod-apply-tasks" / TASK / "status.json", {
            "taskId": TASK, "operation": "modApply", "status": "failed", "stage": "verify",
            "target": {"hostId": HOST, "instanceId": INSTANCE}, "createdAt": "2026-10-31T00:00:00Z",
            "finishedAt": "2026-10-31T00:00:01Z", "error": {"code": "MOD_APPLY_VERIFY_FAILED"}})
        self.request = {"schemaVersion": 1, "requestId": TASK, "target": {
            "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE},
            "expectedRecordVersion": 1, "logLimit": 200}
        self.docker_output = {
            "ps": "abcdef123456\tmaster\tUp 2 hours\n",
            "logs": "2026-10-31T00:00:00Z token=secret-value\n2026-10-31T00:00:01Z server ready\n",
            "version": "29.0.0\n",
        }

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def docker(self, command, **_kwargs):
        if command[:2] == ["ps", "-a"]:
            return subprocess.CompletedProcess(command, 0, self.docker_output["ps"], "")
        if command[:1] == ["logs"]:
            return subprocess.CompletedProcess(command, 0, self.docker_output["logs"], "")
        if command[:2] == ["version", "--format"]:
            return subprocess.CompletedProcess(command, 0, self.docker_output["version"], "")
        return subprocess.CompletedProcess(command, 1, "", "")

    def test_reports_health_logs_and_failed_tasks(self):
        output = io.StringIO()
        with patch.object(MODULE, "ROOT", self.root), \
             patch.object(MODULE, "docker", side_effect=lambda args, timeout=15: (
                 self.docker_output["ps"] if args[:2] == ["ps", "-a"] else
                 self.docker_output["logs"] if args[:1] == ["logs"] else
                 self.docker_output["version"] if args[:2] == ["version", "--format"] else None)):
            with contextlib.redirect_stdout(output):
                MODULE.main(self.request)
        result = json.loads(output.getvalue())
        self.assertEqual(result["health"]["runtimeStatus"], "running")
        self.assertEqual(result["health"]["dstBuild"], "760944")
        self.assertEqual(result["logs"][0]["shard"], "master")
        self.assertIn("<redacted>", result["logs"][0]["lines"][0])
        self.assertNotIn("secret-value", result["logs"][0]["lines"][0])
        self.assertEqual(result["tasks"][0]["taskId"], TASK)
        self.assertEqual(result["tasks"][0]["errorCode"], "MOD_APPLY_VERIFY_FAILED")


if __name__ == "__main__":
    unittest.main()
