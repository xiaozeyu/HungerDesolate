import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dst_update_check", PROJECT / "remote-scripts/probes/dst-update-check.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
RELEASE = "44444444-4444-4444-8444-444444444444"


class DstUpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dst-update-check-")
        self.root = Path(self.temp.name) / "hub"
        self.updates = self.root / "updates"
        (self.root / "releases").mkdir(parents=True)
        self.write(self.root / "host.json", {"schemaVersion": 1, "hostId": HOST})
        self.write(self.root / "releases" / "catalog.json", {"schemaVersion": 1, "hostId": HOST, "releases": [
            {"releaseId": RELEASE, "dstBuild": "639491", "imageDigest": "sha256:" + "a" * 64,
             "verified": True, "runtimeVerified": False}]})
        self.request = {"schemaVersion": 1, "requestId": REQUEST, "serverId": SERVER,
                        "hostId": HOST, "action": "status"}
        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "UPDATES", self.updates),
            patch.object(MODULE, "CACHE", self.updates / "dst-latest.json"),
            patch.object(MODULE, "LOCK", self.updates / "check.lock"),
            patch.object(MODULE, "image_available", return_value=True),
            patch.object(MODULE, "docker_image_exists", return_value=False),
            patch.object(MODULE, "cleanup_stale_check_containers", return_value=None),
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

    def hold_lock(self):
        self.updates.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.updates / "check.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def running_cache(self, stage="steamcmd"):
        return {"schemaVersion": 1, "hostId": HOST, "result": "running", "stage": stage,
                "startedAt": "2026-11-13T00:00:00Z", "checkedAt": "2026-11-13T00:00:00Z",
                "latestBuild": "639491", "lastSuccessAt": "2026-11-12T00:00:00Z",
                "source": "steamCmd", "sourceImageDigest": "sha256:" + "a" * 64,
                "errorCode": None}

    def test_parses_public_branch_buildid(self):
        output = '"branches"\n{\n"public"\n{\n"buildid" "639491"\n"timeupdated" "1"\n}\n"beta"\n{\n"buildid" "1"\n}\n}'
        self.assertEqual(MODULE.parse_build(output), "639491")

    def test_check_tool_prefers_bootstrapped_build_base(self):
        release = MODULE.catalog_releases()[0]
        release["scriptVersion"] = "V3-test"
        with patch.object(MODULE, "docker_image_exists", return_value=True):
            tool = MODULE.check_tool(release)
        self.assertEqual(tool["image"], "hungerdesolate/dst-build-base:V3-test")
        self.assertEqual(tool["steamcmd"], "/home/steam/steamcmd.sh")
        self.assertEqual(tool["home"], "/home/steam")

    def test_check_tool_falls_back_to_release_image(self):
        release = MODULE.catalog_releases()[0]
        with patch.object(MODULE, "docker_image_exists", return_value=False):
            tool = MODULE.check_tool(release)
        self.assertEqual(tool["image"], release["imageDigest"])
        self.assertEqual(tool["steamcmd"], MODULE.STEAMCMD)
        self.assertEqual(tool["home"], "/home/famine")

    def test_check_writes_cache_and_status_reads_it(self):
        with patch.object(MODULE, "run_steamcmd", return_value="639491"):
            data = MODULE.check(dict(self.request, action="check"))
        self.assertEqual(data["latest"]["build"], "639491")
        self.assertEqual(len(data["candidateReleases"]), 1)
        self.assertFalse(data["checking"])
        cache = json.loads((self.updates / "dst-latest.json").read_text())
        self.assertEqual(cache["result"], "ok")
        self.assertEqual(cache["lastSuccessAt"], cache["checkedAt"])
        self.assertEqual(cache["startedAt"], cache["checkedAt"])

    def test_failed_check_preserves_last_successful_build(self):
        with patch.object(MODULE, "run_steamcmd", return_value="639491"):
            MODULE.check(dict(self.request, action="check"))
        with patch.object(MODULE, "run_steamcmd", side_effect=MODULE.Rejected("DST_UPDATE_CHECK_FAILED")):
            data = MODULE.check(dict(self.request, action="check"))
        self.assertEqual(data["latest"]["build"], "639491")
        self.assertEqual(data["lastCheckError"]["code"], "DST_UPDATE_CHECK_FAILED")
        self.assertEqual(data["lastCheckError"]["message"], "SteamCMD 未能获取 DST 最新版本")
        self.assertFalse(data["checking"])
        self.assertEqual(len(data["candidateReleases"]), 1)

    def test_running_check_is_reported_and_second_start_is_noop(self):
        fd = self.hold_lock()
        try:
            self.write(self.updates / "dst-latest.json", self.running_cache())
            status = MODULE.status_data(HOST, MODULE.catalog_releases())
            self.assertTrue(status["checking"])
            self.assertEqual(status["stage"], "steamcmd")
            self.assertEqual(status["startedAt"], "2026-11-13T00:00:00Z")
            with patch.object(MODULE, "run_steamcmd", return_value="639491") as run:
                data = MODULE.check(dict(self.request, action="check"))
            self.assertTrue(data["checking"])
            run.assert_not_called()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_running_cache_without_lock_is_reported_as_interrupted(self):
        self.write(self.updates / "dst-latest.json", self.running_cache())
        status = MODULE.status_data(HOST, MODULE.catalog_releases())
        self.assertFalse(status["checking"])
        self.assertEqual(status["lastCheckError"]["code"], "DST_UPDATE_CHECK_INTERRUPTED")
        self.assertEqual(status["lastCheckError"]["message"], "上次检查中断，请重试")

    def test_image_selection_failure_is_persisted(self):
        with patch.object(MODULE, "check_image", side_effect=MODULE.Rejected("DST_UPDATE_CHECK_UNAVAILABLE")), \
             patch.object(MODULE, "run_steamcmd", return_value="639491") as run:
            data = MODULE.check(dict(self.request, action="check"))
        self.assertEqual(data["lastCheckError"]["code"], "DST_UPDATE_CHECK_UNAVAILABLE")
        self.assertEqual(data["lastCheckError"]["message"], "没有可用于检查的已验证 Release 镜像")
        self.assertFalse(data["checking"])
        cache = json.loads((self.updates / "dst-latest.json").read_text())
        self.assertEqual(cache["result"], "failed")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
