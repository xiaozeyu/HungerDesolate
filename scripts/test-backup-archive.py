"""Exercise the backup archive safety boundary on an isolated filesystem."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backup_archive", PROJECT / "remote-scripts/actions/backup-archive.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
BACKUP = "33333333-3333-4333-8333-333333333333"


class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup-archive-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "Master").mkdir(parents=True)
        (self.source / "config" / "Cluster_1" / "Master").mkdir(parents=True)
        (self.source / "Master" / "server.ini").write_text("[NETWORK]\nserver_port=11000\n")
        (self.source / "config" / "Cluster_1" / "Master" / "server.ini").write_text("[NETWORK]\nserver_port=11000\n")
        (self.source / "cluster_token.txt").write_text("never archive this")
        self.archive = self.root / "backup.tar.gz"
        self.identity = self.identity_for("quasiHot", "running", False)

    def identity_for(self, mode, runtime_status, verified):
        return {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
                "backupId": BACKUP, "captureMode": mode, "runtimeStatus": runtime_status,
                "runtimeVerified": verified}

    def tearDown(self):
        self.temp.cleanup()

    def test_create_verify_and_extract_excludes_secret_files(self):
        created = MODULE.create_archive(self.source, self.archive, self.identity)
        verified = MODULE.inspect_archive(self.archive, HOST, INSTANCE, created["checksum"])
        staging = self.root / "staging"
        MODULE.extract_to_staging(self.archive, staging, HOST, INSTANCE, created["checksum"])
        self.assertEqual(verified["manifest"]["fileCount"], 2)
        self.assertEqual((staging / "Master" / "server.ini").read_text(), "[NETWORK]\nserver_port=11000\n")
        self.assertTrue((staging / "config" / "Cluster_1" / "Master" / "server.ini").exists())
        self.assertFalse((staging / "cluster_token.txt").exists())
        self.assertEqual(verified["manifest"]["captureSemantics"], "onDiskBestEffort")
        self.assertIn("config/Cluster_1/Master/server.ini",
                      [item["path"] for item in verified["manifest"]["files"]])

    def test_quasihot_archive_hashes_the_bytes_captured_in_one_read_pass(self):
        archive = self.root / "changing.tar.gz"
        original = MODULE.checked_open
        changed = False
        path = self.source / "Master" / "server.ini"
        def change_before_capture(source_path, device, inode):
            nonlocal changed
            if source_path == path and not changed:
                changed = True
                path.write_text("[NETWORK]\nserver_port=12000\n")
            return original(source_path, device, inode)
        with patch.object(MODULE, "checked_open", side_effect=change_before_capture):
            created = MODULE.create_archive(self.source, archive, self.identity)
        extracted = self.root / "changed-stage"
        MODULE.extract_to_staging(archive, extracted, HOST, INSTANCE, created["checksum"])
        self.assertIn("server_port=12000", (extracted / "Master" / "server.ini").read_text())

    def test_cold_archive_requires_verified_stopped_runtime(self):
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.create_archive(self.source, self.archive,
                                  self.identity_for("cold", "running", True))
        self.assertEqual(failure.exception.code, "BACKUP_COLD_REQUIRES_VERIFIED_STOP")
        created = MODULE.create_archive(self.source, self.archive,
                                        self.identity_for("cold", "stopped", True))
        self.assertEqual(created["manifest"]["captureSemantics"], "stoppedBeforeRead")

    def test_rejects_wrong_instance_and_checksum_mismatch(self):
        created = MODULE.create_archive(self.source, self.archive, self.identity)
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.inspect_archive(self.archive, HOST, "44444444-4444-4444-8444-444444444444")
        self.assertEqual(failure.exception.code, "BACKUP_TARGET_MISMATCH")
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.inspect_archive(self.archive, HOST, INSTANCE, "0" * 64)
        self.assertEqual(failure.exception.code, "BACKUP_CHECKSUM_MISMATCH")
        self.assertTrue(created["checksum"])

    def test_rejects_source_symlinks(self):
        (self.source / "linked.ini").symlink_to(self.source / "Master" / "server.ini")
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.create_archive(self.source, self.archive, self.identity)
        self.assertEqual(failure.exception.code, "BACKUP_SOURCE_UNSAFE")

    def test_selected_world_roots_keep_instance_relative_paths(self):
        selected = self.root / "selected.tar.gz"
        created = MODULE.create_archive(self.source, selected, self.identity, ["Master"])
        self.assertEqual(created["manifest"]["files"][0]["path"], "Master/server.ini")
        self.assertEqual(created["manifest"]["fileCount"], 1)

    def test_selected_metadata_files_keep_their_instance_relative_paths(self):
        metadata = self.source / "desired" / "world.json"
        metadata.parent.mkdir()
        metadata.write_text('{"schemaVersion":1}')
        selected = self.root / "metadata.tar.gz"
        created = MODULE.create_archive(self.source, selected, self.identity, ["desired/world.json"])
        self.assertEqual(created["manifest"]["files"][0]["path"], "desired/world.json")
        staging = self.root / "metadata-stage"
        MODULE.extract_to_staging(selected, staging, HOST, INSTANCE, created["checksum"])
        self.assertEqual((staging / "desired" / "world.json").read_text(), '{"schemaVersion":1}')

    def test_rejects_archive_path_traversal_before_extracting(self):
        bad = self.root / "bad.tar.gz"
        manifest = {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
                    "fileCount": 1, "sizeBytes": 1,
                    "files": [{"path": "../escape", "sizeBytes": 1, "sha256": "0" * 64, "mode": 0o600}]}
        with tarfile.open(bad, "w:gz") as archive:
            for name, payload in (("data/../escape", b"x"), ("backup-manifest.json", MODULE.canonical(manifest))):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.inspect_archive(bad, HOST, INSTANCE)
        self.assertEqual(failure.exception.code, "BACKUP_ARCHIVE_UNSAFE")
        self.assertFalse((self.root.parent / "escape").exists())

    def test_rejects_manifest_size_expansion_before_reading_payload(self):
        created = MODULE.create_archive(self.source, self.archive, self.identity)
        altered = self.root / "altered.tar.gz"
        manifest = dict(created["manifest"])
        manifest["files"] = [dict(item) for item in created["manifest"]["files"]]
        manifest["files"][0]["sizeBytes"] = MODULE.MAX_BYTES + 1
        manifest["sizeBytes"] = MODULE.MAX_BYTES + 1 + manifest["files"][1]["sizeBytes"]
        with tarfile.open(self.archive, "r:gz") as source, tarfile.open(altered, "w:gz") as target:
            for member in source.getmembers():
                payload = (MODULE.canonical(manifest) if member.name == "backup-manifest.json"
                           else source.extractfile(member).read())
                info = tarfile.TarInfo(member.name)
                info.size = len(payload)
                info.mode = member.mode
                target.addfile(info, io.BytesIO(payload))
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.inspect_archive(altered, HOST, INSTANCE)
        self.assertEqual(failure.exception.code, "BACKUP_SIZE_LIMIT")

    def test_rejects_non_empty_staging_directory(self):
        created = MODULE.create_archive(self.source, self.archive, self.identity)
        staging = self.root / "staging"
        staging.mkdir()
        (staging / "keep").write_text("user data")
        with self.assertRaises(MODULE.BackupRejected) as failure:
            MODULE.extract_to_staging(self.archive, staging, HOST, INSTANCE, created["checksum"])
        self.assertEqual(failure.exception.code, "BACKUP_STAGING_NOT_EMPTY")
        self.assertEqual((staging / "keep").read_text(), "user data")


if __name__ == "__main__":
    unittest.main()
