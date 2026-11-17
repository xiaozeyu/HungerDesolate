import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("host_mods_download", PROJECT / "remote-scripts/actions/host-mods-download.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
SERVER = "33333333-3333-4333-8333-333333333333"
TASK = "44444444-4444-4444-8444-444444444444"


class HostModsDownloadTests(unittest.TestCase):
    def test_request_requires_unique_numeric_workshop_ids(self):
        request = {"schemaVersion": 1, "requestId": TASK, "operationId": TASK, "serverId": SERVER,
                   "hostId": HOST, "expectedRecordVersion": 1, "workshopIds": ["123", "456"]}
        MODULE.valid_request(request)
        request["workshopIds"] = ["123", "123"]
        with self.assertRaises(MODULE.Rejected):
            MODULE.valid_request(request)
        request["workshopIds"] = ["not-a-number"]
        with self.assertRaises(MODULE.Rejected):
            MODULE.valid_request(request)

    def test_content_files_requires_modinfo_and_returns_digest(self):
        with tempfile.TemporaryDirectory(prefix="host-mod-content-") as temporary:
            root = Path(temporary) / "content"
            root.mkdir()
            (root / "modinfo.lua").write_text("name='x'\n")
            (root / "scripts").mkdir()
            (root / "scripts" / "main.lua").write_text("return true\n")
            files, total, digest = MODULE.content_files(root)
            self.assertEqual(len(files), 2)
            self.assertGreater(total, 0)
            self.assertEqual(len(digest), 64)

    def test_modinfo_ignores_indented_option_names(self):
        with tempfile.TemporaryDirectory(prefix="host-mod-modinfo-") as temporary:
            root = Path(temporary)
            (root / "modinfo.lua").write_text(
                "local L = locale ~= \"zh\" and locale ~= \"zhr\"\n"
                "name = L and \"CookDiary (Controller Fix)\" or \"馨食记（手柄修复）\"\n"
                "author = \"棒打鲜橙\"\n"
                "configuration_options = {\n"
                "    L and {\n"
                "        name = \"Language\",\n"
                "    } or {\n"
                "        name = \"Language\",\n"
                "    },\n"
                "}\n")
            self.assertEqual(MODULE.modinfo_fields(root),
                             {"name": "馨食记（手柄修复）", "author": "棒打鲜橙"})

    def test_worker_entrypoint_dispatches_systemd_worker_arguments(self):
        source = (PROJECT / "remote-scripts/actions/host-mods-download.py").read_text()
        self.assertIn('sys.argv[1] == "worker"', source)
        self.assertIn("worker(sys.argv[2])", source)

    def test_auto_cleanup_passes_current_versions_and_parses_deleted(self):
        with tempfile.TemporaryDirectory(prefix="host-mod-cleanup-") as temporary:
            directory = Path(temporary)
            (directory / "probe.py").write_text(
                "import json, sys\n"
                "request = json.load(sys.stdin)\n"
                "assert request['action'] == 'cleanup'\n"
                "assert request['keep'] == [{'workshopId': '123', 'contentDigest': 'a' * 64}]\n"
                "print(json.dumps({'ok': True, 'data': {'deleted': [\n"
                "    {'workshopId': '123', 'contentDigest': 'b' * 64}]}}))\n")
            request = {"schemaVersion": 1, "requestId": TASK, "serverId": SERVER, "hostId": HOST,
                       "expectedRecordVersion": 1}
            deleted = MODULE.run_library_cleanup(
                directory, request, [{"workshopId": "123", "contentDigest": "a" * 64}])
            self.assertEqual(deleted, [{"workshopId": "123", "contentDigest": "b" * 64}])

    def test_task_result_identifies_host_domain(self):
        request = {"serverId": SERVER, "hostId": HOST, "workshopIds": ["123"]}
        result = MODULE.task_result(request, [{"workshopId": "123"}])
        self.assertEqual(result["invalidate"], [{"hostId": HOST, "domains": ["mods"]}])
        self.assertTrue(result["remoteChanged"])


if __name__ == "__main__":
    unittest.main()
