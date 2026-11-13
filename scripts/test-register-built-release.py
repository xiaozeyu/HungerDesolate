"""Test atomic registration of a completed DST image build."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "register_built_release", PROJECT / "remote-scripts/actions/register-built-release.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
RELEASE_ID = "22222222-2222-4222-8222-222222222222"
DIGEST = "sha256:" + "a" * 64


class RegisterBuiltReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="register-built-release-")
        self.root = Path(self.temp.name) / "famine-hub"
        (self.root / "staging" / RELEASE_ID).mkdir(parents=True, mode=0o755)
        (self.root / "staging").chmod(0o755)
        (self.root / "releases").mkdir(mode=0o755)
        host_file = self.root / "host.json"
        host_file.write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        host_file.chmod(0o600)
        metadata = {
            "schemaVersion": 1,
            "releaseId": RELEASE_ID,
            "platform": "linux/amd64",
            "image": "hungerdesolate/dst-runtime",
            "imageDigest": DIGEST,
            "dstBuild": "build-1",
            "steamBuildId": "26474880",
            "artifactDigest": DIGEST,
            "scriptVersion": "V2-test",
            "verified": False,
            "runtimeVerified": False,
            "commands": {
                "master": ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64", "master"],
                "caves": ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64", "caves"],
            },
        }
        metadata_file = self.root / "staging" / RELEASE_ID / "release-metadata.json"
        metadata_file.write_text(json.dumps(metadata))
        metadata_file.chmod(0o600)
        original_lstat, original_fstat = Path.lstat, os.fstat

        def root_owned(path):
            values = list(original_lstat(path))
            values[4] = 0
            return os.stat_result(values)

        def root_fstat(fd):
            values = list(original_fstat(fd))
            values[4] = 0
            return os.stat_result(values)

        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "STAGING", self.root / "staging"),
            patch.object(MODULE, "RELEASES", self.root / "releases"),
            patch.object(MODULE, "CATALOG", self.root / "releases" / "catalog.json"),
            patch.object(MODULE, "LOCK", self.root / "releases" / "catalog.lock"),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", root_owned),
            patch.object(MODULE.os, "fstat", root_fstat),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_registers_base_verified_build_as_unverified_at_runtime(self):
        def docker_result(command, **_kwargs):
            if "image" in command:
                return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
            if "/srv/capabilities/workshop-fetch" in command[-1]:
                return SimpleNamespace(returncode=0, stdout="steamcmd-workshop-v1\n")
            if "/bin/sh" in command:
                return SimpleNamespace(returncode=0, stdout="build-1\n")
            return SimpleNamespace(returncode=0, stdout="")

        with patch.object(MODULE.subprocess, "run", side_effect=docker_result):
            MODULE.register(RELEASE_ID)
            MODULE.register(RELEASE_ID)

        catalog = json.loads(MODULE.CATALOG.read_text())
        self.assertEqual(catalog["hostId"], HOST)
        self.assertEqual(len(catalog["releases"]), 1)
        release = catalog["releases"][0]
        self.assertEqual(release["releaseId"], RELEASE_ID)
        self.assertEqual(release["steamBuildId"], "26474880")
        self.assertTrue(release["verified"])
        self.assertFalse(release["runtimeVerified"])
        self.assertEqual((self.root / "releases").stat().st_mode & 0o777, 0o700)

    def test_shared_dst_directory_registers_with_content_digest(self):
        metadata = json.loads((self.root / "staging" / RELEASE_ID / "release-metadata.json").read_text())
        dst = self.root / "dst" / RELEASE_ID
        (dst / "bin64").mkdir(parents=True, mode=0o700)
        (dst / "data" / "databundles").mkdir(parents=True, mode=0o700)
        (dst / "version.txt").write_text("build-1\n")
        binary = dst / "bin64" / "dontstarve_dedicated_server_nullrenderer_x64"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        (dst / "data" / "databundles" / "scripts.zip").write_bytes(b"scripts")
        metadata["artifactDigest"] = "sha256:" + MODULE.content_digest(dst)
        (self.root / "staging" / RELEASE_ID / "release-metadata.json").write_text(json.dumps(metadata))
        responses = [
            SimpleNamespace(returncode=0, stdout=DIGEST + "\n"),
            SimpleNamespace(returncode=0, stdout="build-1\n"),
            SimpleNamespace(returncode=0, stdout="libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6\n"),
            SimpleNamespace(returncode=0, stdout="steamcmd-workshop-v1\n"),
        ]
        with patch.object(MODULE, "DST", self.root / "dst"), \
             patch.object(MODULE.subprocess, "run", side_effect=responses):
            MODULE.register(RELEASE_ID)
        release = json.loads(MODULE.CATALOG.read_text())["releases"][0]
        self.assertEqual(release["image"], "hungerdesolate/dst-runtime")
        self.assertEqual(release["artifactDigest"], metadata["artifactDigest"])

    def test_digest_mismatch_prevents_catalog_registration(self):
        result = SimpleNamespace(returncode=0, stdout="sha256:" + "b" * 64 + "\n")
        with patch.object(MODULE.subprocess, "run", return_value=result):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.register(RELEASE_ID)
        self.assertEqual(str(failure.exception), "CREATE_RELEASE_IMAGE_DIGEST_MISMATCH")
        self.assertFalse(MODULE.CATALOG.exists())

    def test_binary_check_inspects_dynamic_libraries_without_running_dst(self):
        metadata = json.loads((self.root / "staging" / RELEASE_ID / "release-metadata.json").read_text())
        responses = [
            SimpleNamespace(returncode=0, stdout=DIGEST + "\n"),
            SimpleNamespace(returncode=0, stdout="build-1\n"),
            SimpleNamespace(returncode=0, stdout="libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6\n"),
            SimpleNamespace(returncode=0, stdout="steamcmd-workshop-v1\n"),
        ]
        with patch.object(MODULE.subprocess, "run", side_effect=responses) as run:
            MODULE.verify_image(metadata)

        binary_command = run.call_args_list[2].args[0]
        self.assertIn("ldd /srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64", binary_command[-1])
        self.assertNotIn("-version", binary_command)

    def test_binary_check_rejects_missing_runtime_libraries(self):
        metadata = json.loads((self.root / "staging" / RELEASE_ID / "release-metadata.json").read_text())
        responses = [
            SimpleNamespace(returncode=0, stdout=DIGEST + "\n"),
            SimpleNamespace(returncode=0, stdout="build-1\n"),
            SimpleNamespace(returncode=0, stdout="libcurl-gnutls.so.4 => not found\n"),
            SimpleNamespace(returncode=0, stdout="steamcmd-workshop-v1\n"),
        ]
        with patch.object(MODULE.subprocess, "run", side_effect=responses):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.verify_image(metadata)
        self.assertEqual(str(failure.exception), "CREATE_RELEASE_BINARY_CHECK_FAILED")

    def test_registration_requires_workshop_fetch_capability(self):
        responses = [
            SimpleNamespace(returncode=0, stdout=DIGEST + "\n"),
            SimpleNamespace(returncode=0, stdout="build-1\n"),
            SimpleNamespace(returncode=0, stdout="libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
        with patch.object(MODULE.subprocess, "run", side_effect=responses):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.register(RELEASE_ID)
        self.assertEqual(str(failure.exception), "CREATE_RELEASE_WORKSHOP_FETCH_MISSING")

    def test_revalidates_an_existing_unverified_catalog_entry(self):
        metadata_path = self.root / "staging" / RELEASE_ID / "release-metadata.json"
        metadata = json.loads(metadata_path.read_text())
        existing = MODULE.release_entry(metadata, RELEASE_ID)
        existing["verified"] = False
        catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [existing]}
        MODULE.CATALOG.write_text(json.dumps(catalog))
        MODULE.CATALOG.chmod(0o600)

        def docker_result(command, **_kwargs):
            if "image" in command:
                return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
            if "/srv/capabilities/workshop-fetch" in command[-1]:
                return SimpleNamespace(returncode=0, stdout="steamcmd-workshop-v1\n")
            if "/bin/sh" in command:
                return SimpleNamespace(returncode=0, stdout="build-1\n")
            return SimpleNamespace(returncode=0, stdout="")

        with patch.object(MODULE.subprocess, "run", side_effect=docker_result):
            MODULE.register(RELEASE_ID)

        updated = json.loads(MODULE.CATALOG.read_text())["releases"][0]
        self.assertTrue(updated["verified"])
        self.assertFalse(updated["runtimeVerified"])

    def test_rejects_metadata_for_a_different_release_id(self):
        metadata_path = self.root / "staging" / RELEASE_ID / "release-metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["releaseId"] = "33333333-3333-4333-8333-333333333333"
        metadata_path.write_text(json.dumps(metadata))
        metadata_path.chmod(0o600)

        with self.assertRaises(MODULE.Rejected):
            MODULE.register(RELEASE_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
