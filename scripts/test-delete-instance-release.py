"""Test reference-checked Release deletion and exact Docker image removal."""
import importlib.util
import json
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "delete_instance_release", PROJECT / "remote-scripts/actions/delete-instance-release.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOST = "11111111-1111-4111-8111-111111111111"
RELEASE = "22222222-2222-4222-8222-222222222222"
DIGEST = "sha256:" + "a" * 64
ENTRY = {"releaseId": RELEASE, "platform": "linux/amd64", "image": "hungerdesolate/dst-runtime",
         "imageDigest": DIGEST, "dstBuild": "756039", "artifactDigest": DIGEST,
         "scriptVersion": "build-1", "verified": True, "runtimeVerified": False,
         "commands": {"master": ["/srv/dst"], "caves": ["/srv/dst"]}}


class ReleaseDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delete-release-")
        self.root = Path(self.temp.name) / "famine-hub"
        (self.root / "releases").mkdir(parents=True)
        (self.root / "staging" / RELEASE).mkdir(parents=True)
        (self.root / "locks").mkdir()
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        self.catalog = {"schemaVersion": 1, "hostId": HOST, "releases": [ENTRY]}
        (self.root / "releases" / "catalog.json").write_text(json.dumps(self.catalog))
        (self.root / "registry.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST, "instances": []}))
        metadata = dict(ENTRY, schemaVersion=1)
        (self.root / "staging" / RELEASE / "release-metadata.json").write_text(json.dumps(metadata))
        for path in self.root.rglob("*"):
            if path.is_file(): path.chmod(0o600)
            elif path.is_dir(): path.chmod(0o700)
        self.original_lstat, self.original_fstat = Path.lstat, os.fstat
        def root_path(path):
            values = list(self.original_lstat(path)); values[4] = 0
            return os.stat_result(values)
        def root_fd(fd):
            values = list(self.original_fstat(fd)); values[4] = 0
            return os.stat_result(values)
        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE, "CATALOG", self.root / "releases" / "catalog.json"),
            patch.object(MODULE, "REGISTRY", self.root / "registry.json"),
            patch.object(MODULE, "RELEASES", self.root / "releases"),
            patch.object(MODULE, "STAGING", self.root / "staging"),
            patch.object(MODULE, "REGISTRY_LOCK", self.root / "locks" / "registry.lock"),
            patch.object(MODULE, "CATALOG_LOCK", self.root / "releases" / "catalog.lock"),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", root_path),
            patch.object(MODULE.os, "fstat", root_fd),
        ]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def request(self, action="preview"):
        value = {"action": action, "hostId": HOST, "releaseId": RELEASE}
        if action == "delete":
            value.update({"catalogDigest": MODULE.digest(self.catalog), "typedName": "756039"})
        return value

    def docker_stub(self, command, **_kwargs):
        if command[:3] == ["docker", "container", "ls"]:
            return SimpleNamespace(returncode=0, stdout="")
        if command[:3] == ["docker", "image", "rm"]:
            return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
        raise AssertionError(command)

    def test_preview_and_delete_remove_registration_staging_and_exact_image(self):
        with patch.object(MODULE, "docker", side_effect=self.docker_stub) as docker:
            preview = MODULE.inspect_release(self.request())
            encoded_preview = json.dumps(preview)
            self.assertTrue(preview["allowed"])
            self.assertEqual(json.loads(encoded_preview)["instanceReferences"], [])
            result = MODULE.remove(self.request("delete"))
        self.assertTrue(result["imageRemoved"])
        self.assertEqual(json.loads(MODULE.CATALOG.read_text())["releases"], [])
        self.assertFalse((self.root / "staging" / RELEASE).exists())
        self.assertEqual(docker.call_args.args[0], ["docker", "image", "rm", DIGEST])

    def test_instance_reference_blocks_deletion(self):
        registry = {"schemaVersion": 1, "hostId": HOST,
                    "instances": [{"instanceId": "33333333-3333-4333-8333-333333333333", "dstReleaseId": RELEASE}]}
        MODULE.REGISTRY.write_text(json.dumps(registry)); MODULE.REGISTRY.chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_IN_USE")
        self.assertEqual(len(json.loads(MODULE.CATALOG.read_text())["releases"]), 1)

    def test_task_and_container_references_block_deletion(self):
        task_id = "33333333-3333-4333-8333-333333333333"
        task_dir = self.root / "tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "release.json").write_text(json.dumps({"releaseId": RELEASE}))
        (task_dir / "status.json").write_text(json.dumps({"status": "running"}))
        (task_dir / "release.json").chmod(0o600)
        (task_dir / "status.json").chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_IN_USE")
        shutil.rmtree(self.root / "tasks")

        def referenced_container(command, **_kwargs):
            if command[:3] == ["docker", "container", "ls"]:
                return SimpleNamespace(returncode=0, stdout="a" * 64 + "\n")
            if command[:3] == ["docker", "container", "inspect"]:
                return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
            raise AssertionError(command)
        with patch.object(MODULE, "docker", side_effect=referenced_container):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_IN_USE")

    def test_failed_image_removal_restores_catalog_and_keeps_staging(self):
        def docker(command, **_kwargs):
            if command[:3] == ["docker", "container", "ls"]:
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=1, stdout="", stderr="daemon error")
        with patch.object(MODULE, "docker", side_effect=docker):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_DELETE_IMAGE_FAILED")
        self.assertEqual(len(json.loads(MODULE.CATALOG.read_text())["releases"]), 1)
        self.assertTrue((self.root / "staging" / RELEASE).exists())

    def test_missing_image_is_treated_as_already_removed(self):
        def docker(command, **_kwargs):
            if command[:3] == ["docker", "container", "ls"]:
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=1, stdout="", stderr="Error: No such image: " + DIGEST)
        with patch.object(MODULE, "docker", side_effect=docker):
            result = MODULE.remove(self.request("delete"))
        self.assertTrue(result["imageRemoved"])
        self.assertEqual(json.loads(MODULE.CATALOG.read_text())["releases"], [])

    def test_referenced_image_during_remove_rolls_back_catalog(self):
        def docker(command, **_kwargs):
            if command[:3] == ["docker", "container", "ls"]:
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=1, stdout="", stderr="image is being used by stopped container")
        with patch.object(MODULE, "docker", side_effect=docker):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_DELETE_IMAGE_IN_USE")
        self.assertEqual(len(json.loads(MODULE.CATALOG.read_text())["releases"]), 1)

    def test_shared_digest_is_not_removed_from_docker(self):
        shared = dict(ENTRY, releaseId="44444444-4444-4444-8444-444444444444")
        self.catalog["releases"].append(shared)
        MODULE.CATALOG.write_text(json.dumps(self.catalog)); MODULE.CATALOG.chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub) as docker:
            result = MODULE.remove(self.request("delete"))
        self.assertTrue(result["imageShared"])
        self.assertFalse(result["imageRemoved"])
        self.assertFalse(any(call.args[0][:3] == ["docker", "image", "rm"] for call in docker.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
