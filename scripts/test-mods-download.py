import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mods_download", PROJECT / "remote-scripts/actions/mods-download.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
SERVER = "33333333-3333-4333-8333-333333333333"
TASK = "44444444-4444-4444-8444-444444444444"
RELEASE = "55555555-5555-4555-8555-555555555555"
DIGEST = "sha256:" + "a" * 64
FETCH_DIGEST = "sha256:" + "b" * 64
FETCH_RELEASE = "66666666-6666-4666-8666-666666666666"


class ModsDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mods-download-")
        self.root = Path(self.temp.name) / "famine-hub"
        self.instance = self.root / "instances" / INSTANCE
        self.task = self.root / "mod-tasks" / TASK
        dirs = [self.root / "instances", self.root / "locks", self.root / "releases", self.root / "mod-tasks",
                self.instance / "locks", self.instance / "desired", self.instance / "staging", self.instance / "mods",
                self.task]
        for path in dirs:
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.chmod(0o700)
        self.root.chmod(0o700)
        self.instance.chmod(0o700)
        self.task.chmod(0o700)
        self.request = {"schemaVersion": 1, "requestId": "66666666-6666-4666-8666-666666666666",
            "operationId": TASK, "serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE,
            "expectedRecordVersion": 1, "expectedRevision": 1, "workshopIds": ["123456"]}
        self.write_json(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write_json(self.root / "registry.json", {"schemaVersion": 1, "hostId": HOST, "instances": [
            {"instanceId": INSTANCE, "lifecycle": "ready", "relativePath": "instances/" + INSTANCE}]})
        self.write_json(self.instance / "manifest.json", {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
            "configRevision": 1})
        self.write_json(self.instance / "active-release.json", {"dstReleaseId": RELEASE})
        self.write_json(self.root / "releases" / "catalog.json", {"releases": [{"releaseId": RELEASE,
            "verified": True, "image": "example/dst", "imageDigest": DIGEST}, {"releaseId": FETCH_RELEASE,
            "verified": True, "image": "example/fetch", "imageDigest": FETCH_DIGEST}]})
        self.write_json(self.instance / "desired" / "mods.json", {"schemaVersion": 1, "instanceId": INSTANCE,
            "revision": 1, "mods": [{"workshopId": "123456", "enabled": True, "configurationOptions": {}}]})
        self.write_json(self.task / "request.json", self.request)
        self.write_json(self.task / "worker-context.json", {"image": "example/fetch@" + FETCH_DIGEST, "dstReleaseId": RELEASE})
        self.write_json(self.task / "status.json", {"schemaVersion": 1, "taskId": TASK, "operationId": TASK,
            "target": {"serverId": SERVER, "hostId": HOST, "instanceId": INSTANCE}, "operation": "modPrepare",
            "status": "queued", "stage": "queued", "sequence": 1, "createdAt": "2026-01-01T00:00:00Z",
            "startedAt": None, "finishedAt": None, "progress": {"kind": "determinate", "completed": 0, "total": 1},
            "result": MODULE.task_result(self.request, []), "error": None})
        (self.task / "journal.jsonl").write_text(json.dumps({"sequence": 1, "status": "queued", "stage": "queued", "at": "2026-01-01T00:00:00Z"}) + "\n")
        self.patch_root_owner()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_text(json.dumps(value)); path.chmod(0o600)

    def patch_root_owner(self):
        original_lstat, original_fstat = Path.lstat, os.fstat
        def owner_path(path):
            values = list(original_lstat(path)); values[4] = 0; return os.stat_result(values)
        def owner_fd(fd):
            values = list(original_fstat(fd)); values[4] = 0; return os.stat_result(values)
        self.patches = [patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "TASKS", self.root / "mod-tasks"),
            patch.object(MODULE.os, "geteuid", return_value=0), patch.object(MODULE.os, "chown", return_value=None),
            patch.object(Path, "lstat", owner_path), patch.object(MODULE.os, "fstat", owner_fd)]
        self.chown_mock = self.patches[3].start()
        for item in self.patches[:3] + self.patches[4:]: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_worker_publishes_only_verified_prepared_content(self):
        def run(command, **_kwargs):
            if command[1:3] == ["image", "inspect"]:
                image_digest = command[command.index("inspect") + 3]
                return subprocess.CompletedProcess(command, 0, image_digest + "\n", "")
            if "--network" in command and "none" in command:
                if "example/dst@" in command:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")
            mount = next(value for value in command if value.startswith("type=bind,src="))
            self.assertFalse(mount.endswith(",rw"), mount)
            self.assertIn(",dst=/work", mount)
            source = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            content = source / "output" / "123456"
            content.mkdir(parents=True)
            (content / "modinfo.lua").write_text("configuration_options = {}\n")
            (content / "modinfo.lua").chmod(0o600)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(MODULE.subprocess, "run", side_effect=run):
            result = MODULE.worker(TASK)
        self.assertEqual(result["status"], "succeeded", result)
        prepared = result["result"]["preparedMods"][0]
        artifact = self.instance / "mods" / "prepared" / "123456" / prepared["contentDigest"]
        self.assertTrue((artifact / "content" / "modinfo.lua").is_file())
        manifest = json.loads((artifact / "manifest.json").read_text())
        self.assertEqual(manifest["contentDigest"], prepared["contentDigest"])
        self.assertEqual(manifest["dstReleaseId"], RELEASE)
        self.assertEqual(manifest["fetcherImage"], "example/fetch@" + FETCH_DIGEST)
        self.assertEqual((artifact / "content").stat().st_mode & 0o777, 0o555)
        self.assertEqual((artifact / "content" / "modinfo.lua").stat().st_mode & 0o777, 0o444)
        self.assertEqual((artifact / "manifest.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue(any(Path(call.args[0]).name == "modinfo.lua" and call.args[1:] == (0, 0)
                            for call in self.chown_mock.call_args_list))
        self.assertFalse((self.instance / "deployed-mods.json").exists())

    def test_worker_records_safe_steamcmd_timeout_diagnostic(self):
        def run(command, **_kwargs):
            if command[1:3] == ["image", "inspect"]:
                image_digest = command[command.index("inspect") + 3]
                return subprocess.CompletedProcess(command, 0, image_digest + "\n", "")
            if "--network" in command and "none" in command:
                return subprocess.CompletedProcess(command, 1 if "example/dst@" in command else 0, "", "")
            return subprocess.CompletedProcess(command, 124, "", "WORKSHOP_DOWNLOAD_FAILED\n")

        with patch.object(MODULE.subprocess, "run", side_effect=run):
            result = MODULE.worker(TASK)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "download")
        self.assertEqual(result["error"]["code"], "MOD_STEAMCMD_TIMEOUT")
        self.assertEqual(result["error"]["details"], {"exitCode": 124})

    def test_worker_classifies_docker_bridge_start_failure_without_raw_output(self):
        def run(command, **_kwargs):
            if command[1:3] == ["image", "inspect"]:
                image_digest = command[command.index("inspect") + 3]
                return subprocess.CompletedProcess(command, 0, image_digest + "\n", "")
            if "--network" in command and "none" in command:
                return subprocess.CompletedProcess(command, 1 if "example/dst@" in command else 0, "", "")
            return subprocess.CompletedProcess(command, 125, "", "failed to create endpoint on bridge network")

        with patch.object(MODULE.subprocess, "run", side_effect=run):
            result = MODULE.worker(TASK)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "MOD_WORKSHOP_CONTAINER_FAILED")
        self.assertEqual(result["error"]["details"], {"exitCode": 125, "failureKind": "network"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
