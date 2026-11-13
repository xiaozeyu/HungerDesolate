"""Test backup list and capture-mode preview against isolated instance data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "instance_backups", PROJECT / "remote-scripts/probes/instance-backups.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"


class InstanceBackupsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-backups-")
        self.root = Path(self.temp.name)
        instance = self.root / "instances" / INSTANCE
        config = instance / "config" / "Cluster_1" / "Master"
        data = instance / "data" / "Master"
        config.mkdir(parents=True)
        data.mkdir(parents=True)
        (config / "server.ini").write_text("[NETWORK]\nserver_port=11000\n")
        (data / "save.zip").write_bytes(b"world")
        files = {
            self.root / "host.json": {"schemaVersion": 1, "hostId": HOST},
            self.root / "registry.json": {"schemaVersion": 1, "hostId": HOST,
                "instances": [{"instanceId": INSTANCE, "lifecycle": "ready"}]},
            instance / "manifest.json": {"schemaVersion": 1, "hostId": HOST,
                "instanceId": INSTANCE, "composeProject": "fh_" + INSTANCE.replace("-", ""),
                "configRevision": 4, "cavesEnabled": False},
        }
        for path, value in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        for path in [self.root, self.root / "instances", instance, instance / "config",
                     instance / "config" / "Cluster_1", config, instance / "data", data]:
            path.chmod(0o700)
        self.config = config.parent
        self.manifest = files[instance / "manifest.json"]
        self.request = {"schemaVersion": 1, "requestId": REQUEST_ID, "serverId": "44444444-4444-4444-8444-444444444444",
                        "hostId": HOST, "instanceId": INSTANCE, "expectedRevision": 4}
        self.root_patch = patch.object(MODULE, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_empty_list_is_real_and_bound_to_target(self):
        result = MODULE.list_backups(dict(self.request))
        self.assertEqual(result["items"], [])
        self.assertEqual(result["target"]["instanceId"], INSTANCE)
        self.assertFalse(result["restoreAcceptancePassed"])

    def test_existing_backup_without_dst_build_resolves_it_from_catalog(self):
        release_id = "55555555-5555-4555-8555-555555555555"
        backup_id = "77777777-7777-4777-8777-777777777777"
        catalog_dir = self.root / "releases"
        catalog_dir.mkdir(mode=0o700)
        catalog = catalog_dir / "catalog.json"
        catalog.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST, "releases": [
            {"releaseId": release_id, "verified": True, "dstBuild": "760944"}]}))
        catalog.chmod(0o600)
        backup = self.root / "backups" / INSTANCE / backup_id
        backup.mkdir(parents=True, mode=0o700)
        metadata = backup / "metadata.json"
        metadata.write_text(json.dumps({"schemaVersion": 1, "backupId": backup_id,
            "hostId": HOST, "instanceId": INSTANCE, "createdAt": "2026-10-19T10:00:00Z",
            "reason": "MOD 应用前保护点", "captureMode": "cold",
            "captureSemantics": "stoppedBeforeRead", "runtimeStatusAtCapture": "stopped",
            "sizeBytes": 1, "checksum": "a" * 64, "verification": "verified",
            "restorability": "incompatible", "dstBuild": None, "dstReleaseId": release_id,
            "modsReleaseId": None, "configRevision": 3, "containsSecrets": False}))
        metadata.chmod(0o600)
        for path in (self.root / "backups", self.root / "backups" / INSTANCE, backup):
            path.chmod(0o700)
        result = MODULE.list_backups(dict(self.request))
        self.assertEqual(result["items"][0]["dstBuild"], "760944")

    def test_restore_acceptance_reads_host_scoped_evidence(self):
        evidence = self.root / "restore-acceptance.json"
        evidence.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
            "instanceId": INSTANCE, "taskId": REQUEST_ID,
            "verifiedAt": "2026-10-22T00:00:00Z", "runtimeVerified": True}))
        evidence.chmod(0o600)
        result = MODULE.list_backups(dict(self.request))
        self.assertTrue(result["restoreAcceptancePassed"])

    def test_preview_distinguishes_hot_capture_and_cold_status_requirement(self):
        with patch.object(MODULE, "runtime_state", return_value=("running", True)):
            hot = MODULE.preview(dict(self.request, captureMode="quasiHot"))
            cold = MODULE.preview(dict(self.request, captureMode="cold"))
        self.assertTrue(hot["allowed"])
        self.assertEqual(hot["warnings"], ["QUASI_HOT_FILES_MAY_CHANGE_DURING_CAPTURE"])
        self.assertTrue(cold["allowed"])
        self.assertEqual(cold["runtimeStatus"], "running")

    def test_cold_preview_blocks_unknown_runtime_but_quasi_hot_stays_available(self):
        with patch.object(MODULE, "runtime_state", return_value=("unknown", False)):
            cold = MODULE.preview(dict(self.request, captureMode="cold"))
            hot = MODULE.preview(dict(self.request, captureMode="quasiHot"))
        self.assertFalse(cold["allowed"])
        self.assertTrue(hot["allowed"])

    def test_cold_preview_allows_known_degraded_containers_for_safe_stop(self):
        with patch.object(MODULE, "runtime_state", return_value=("degraded", True)):
            cold = MODULE.preview(dict(self.request, captureMode="cold"))
        self.assertTrue(cold["allowed"])
        self.assertEqual(cold["runtimeStatus"], "degraded")

    def test_manifest_revision_must_match_request(self):
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.preview(dict(self.request, expectedRevision=3, captureMode="cold"))
        self.assertEqual(str(failure.exception), "INSTANCE_IDENTITY_MISMATCH")

    def test_runtime_owned_data_and_config_directories_are_allowed(self):
        data_root = self.root / "instances" / INSTANCE / "data" / "Master"
        config_root = self.root / "instances" / INSTANCE / "config"
        cluster_root = config_root / "Cluster_1"
        original_lstat = Path.lstat
        def runtime_owned(path):
            values = list(original_lstat(path))
            if path in (data_root, config_root, cluster_root):
                values[4] = MODULE.RUNTIME_UID
            return __import__("os").stat_result(values)
        with patch.object(Path, "lstat", runtime_owned), \
             patch.object(MODULE, "runtime_state", return_value=("running", True)):
            result = MODULE.preview(dict(self.request, captureMode="quasiHot"))
        self.assertTrue(result["allowed"])

    def test_world_data_root_with_group_or_other_write_is_rejected(self):
        data_root = self.root / "instances" / INSTANCE / "data" / "Master"
        data_root.chmod(0o777)
        with patch.object(MODULE, "runtime_state", return_value=("running", True)):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.preview(dict(self.request, captureMode="quasiHot"))
        self.assertEqual(str(failure.exception), "BACKUP_PATH_UNSAFE")

    def test_preview_counts_only_world_data_and_skips_runtime_logs(self):
        data = self.root / "instances" / INSTANCE / "data" / "Master"
        (data / "server_log.txt").write_text("runtime log\n")
        request = dict(self.request, captureMode="quasiHot")
        with patch.object(MODULE, "runtime_state", return_value=("running", True)):
            result = MODULE.preview(request)
        self.assertEqual(result["fileCount"], 1)
        self.assertTrue(result["worldDataPresent"])

    def test_installed_build_reads_the_active_release_catalog_hint(self):
        release_id = "55555555-5555-4555-8555-555555555555"
        (self.root / "releases").mkdir(mode=0o700)
        (self.root / "releases" / "catalog.json").write_text(json.dumps({"schemaVersion": 1,
            "hostId": HOST, "releases": [{"releaseId": release_id, "dstBuild": "760944"}]}))
        (self.root / "releases" / "catalog.json").chmod(0o600)
        instance = self.root / "instances" / INSTANCE
        (instance / "active-release.json").write_text(json.dumps({"dstReleaseId": release_id}))
        (instance / "active-release.json").chmod(0o600)
        self.assertEqual(MODULE.installed_build(instance), "760944")

    def test_backup_delete_allows_restore_protection_points(self):
        backup_id = "77777777-7777-4777-8777-777777777777"
        checksum = "a" * 64
        catalog = self.root / "backups" / INSTANCE / backup_id
        catalog.mkdir(parents=True)
        (catalog / "metadata.json").write_text(json.dumps({"schemaVersion": 1, "backupId": backup_id,
            "hostId": HOST, "instanceId": INSTANCE, "checksum": checksum, "reason": "手动创建",
            "createdAt": "2026-10-19T10:00:00Z"}))
        (catalog / "metadata.json").chmod(0o600)
        (catalog / "archive.tar.gz").write_bytes(b"archive")
        (catalog / "archive.tar.gz").chmod(0o600)
        for path in (self.root / "backups", self.root / "backups" / INSTANCE, catalog):
            path.chmod(0o700)
        (self.root / "locks").mkdir(mode=0o700)
        with patch.object(MODULE, "identity", return_value=(HOST, INSTANCE, self.root / "instances" / INSTANCE, self.manifest)):
            result = MODULE.delete_backup({**self.request, "backupId": backup_id, "expectedChecksum": checksum})
        self.assertTrue(result["deleted"])
        self.assertFalse(catalog.exists())
        catalog.mkdir(mode=0o700)
        (catalog / "metadata.json").write_text(json.dumps({"schemaVersion": 1, "backupId": backup_id,
            "hostId": HOST, "instanceId": INSTANCE, "checksum": checksum, "reason": "恢复前保护点",
            "createdAt": "2026-10-20T10:00:00Z"}))
        (catalog / "metadata.json").chmod(0o600)
        (catalog / "archive.tar.gz").write_bytes(b"archive")
        (catalog / "archive.tar.gz").chmod(0o600)
        newest_id = "88888888-8888-4888-8888-888888888888"
        newest = catalog.parent / newest_id
        newest.mkdir(mode=0o700)
        (newest / "metadata.json").write_text(json.dumps({"schemaVersion": 1, "backupId": newest_id,
            "hostId": HOST, "instanceId": INSTANCE, "checksum": checksum, "reason": "恢复前保护点",
            "createdAt": "2026-10-21T10:00:00Z"}))
        (newest / "metadata.json").chmod(0o600)
        (newest / "archive.tar.gz").write_bytes(b"newer archive")
        (newest / "archive.tar.gz").chmod(0o600)
        with patch.object(MODULE, "identity", return_value=(HOST, INSTANCE, self.root / "instances" / INSTANCE, self.manifest)):
            result = MODULE.delete_backup({**self.request, "backupId": newest_id, "expectedChecksum": checksum})
        self.assertTrue(result["deleted"])
        self.assertFalse(newest.exists())
        with patch.object(MODULE, "identity", return_value=(HOST, INSTANCE, self.root / "instances" / INSTANCE, self.manifest)):
            result = MODULE.delete_backup({**self.request, "backupId": backup_id, "expectedChecksum": checksum})
        self.assertTrue(result["deleted"])


if __name__ == "__main__":
    unittest.main()
