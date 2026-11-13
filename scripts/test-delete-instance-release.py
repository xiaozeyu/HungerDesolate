"""Test reference-checked Release deletion and exact Docker image removal."""
import fcntl
import importlib.util
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
            patch.object(MODULE, "DST", self.root / "dst"),
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
        dst = self.root / "dst" / RELEASE
        dst.mkdir(parents=True)
        (dst / "version.txt").write_text("756039\n")
        (dst / "version.txt").chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub) as docker:
            preview = MODULE.inspect_release(self.request())
            encoded_preview = json.dumps(preview)
            self.assertTrue(preview["allowed"])
            self.assertEqual(json.loads(encoded_preview)["instanceReferences"], [])
            result = MODULE.remove(self.request("delete"))
        self.assertTrue(result["imageRemoved"])
        self.assertEqual(json.loads(MODULE.CATALOG.read_text())["releases"], [])
        self.assertFalse((self.root / "staging" / RELEASE).exists())
        self.assertTrue(result["dstRemoved"])
        self.assertFalse(dst.exists())
        self.assertEqual(docker.call_args.args[0], ["docker", "image", "rm", DIGEST])

    def test_active_build_blocks_preview_and_delete(self):
        build = self.root / "image-build"
        build.mkdir(mode=0o700)
        lock_path = build / "build.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(MODULE, "docker", side_effect=self.docker_stub):
                preview = MODULE.inspect_release(self.request())
                self.assertTrue(preview["buildActive"])
                self.assertFalse(preview["allowed"])
                with self.assertRaises(MODULE.Rejected) as failure:
                    MODULE.remove(self.request("delete"))
            self.assertEqual(str(failure.exception), "RELEASE_DELETE_BUILD_ACTIVE")
            self.assertEqual(len(json.loads(MODULE.CATALOG.read_text())["releases"]), 1)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_instance_reference_blocks_deletion(self):
        registry = {"schemaVersion": 1, "hostId": HOST,
                    "instances": [{"instanceId": "33333333-3333-4333-8333-333333333333", "dstReleaseId": RELEASE}]}
        MODULE.REGISTRY.write_text(json.dumps(registry)); MODULE.REGISTRY.chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_IN_USE")
        self.assertEqual(len(json.loads(MODULE.CATALOG.read_text())["releases"]), 1)

    def test_retired_instance_history_does_not_block_or_modify_archived_data(self):
        instance_id = "33333333-3333-4333-8333-333333333333"
        archive = self.root / "retired" / instance_id
        archive.mkdir(parents=True)
        save = archive / "save.dat"
        save.write_bytes(b"archived world")
        registry = {"schemaVersion": 1, "hostId": HOST, "instances": [
            {"instanceId": instance_id, "dstReleaseId": RELEASE, "lifecycle": "retired",
             "relativePath": "retired/" + instance_id}]}
        MODULE.REGISTRY.write_text(json.dumps(registry))
        before = MODULE.REGISTRY.read_bytes()
        with patch.object(MODULE, "docker", side_effect=self.docker_stub):
            preview = MODULE.inspect_release(self.request())
            self.assertEqual(preview["instanceReferences"], [])
            self.assertTrue(preview["allowed"])
            MODULE.remove(self.request("delete"))
        self.assertEqual(MODULE.REGISTRY.read_bytes(), before)
        self.assertEqual(save.read_bytes(), b"archived world")

    def test_active_release_pointer_blocks_even_without_registry_release_field(self):
        instance_id = "33333333-3333-4333-8333-333333333333"
        instance = self.root / "instances" / instance_id
        instance.mkdir(parents=True)
        active = instance / "active-release.json"
        active.write_text(json.dumps({"dstReleaseId": RELEASE}))
        active.chmod(0o600)
        with patch.object(MODULE, "docker", side_effect=self.docker_stub):
            preview = MODULE.inspect_release(self.request())
        self.assertFalse(preview["allowed"])
        self.assertEqual(preview["instanceReferences"], [instance_id])

    def test_completed_task_history_does_not_block_but_unknown_tasks_do(self):
        task_id = "33333333-3333-4333-8333-333333333333"
        task_dir = self.root / "tasks" / task_id
        task_dir.mkdir(parents=True)
        release = task_dir / "release.json"
        release.write_text(json.dumps({"releaseId": RELEASE}))
        release.chmod(0o600)
        status = task_dir / "status.json"
        for state in ("succeeded", "failed", "cancelled", "interrupted", "running", "queued"):
            with self.subTest(status=state):
                status.write_text(json.dumps({"status": state}))
                status.chmod(0o600)
                with patch.object(MODULE, "docker", side_effect=self.docker_stub):
                    preview = MODULE.inspect_release(self.request())
                terminal = state in ("succeeded", "failed", "cancelled")
                self.assertEqual(preview["allowed"], terminal)
                self.assertEqual(preview["taskReferences"], [] if terminal else [task_id])

    def test_reference_appearing_after_preview_is_rechecked_before_removal(self):
        scans = 0

        def docker(command, **kwargs):
            nonlocal scans
            if command[:3] == ["docker", "container", "ls"]:
                scans += 1
                if scans == 1:
                    registry = {"schemaVersion": 1, "hostId": HOST, "instances": [
                        {"instanceId": "33333333-3333-4333-8333-333333333333",
                         "lifecycle": "ready", "dstReleaseId": RELEASE}]}
                    MODULE.REGISTRY.write_text(json.dumps(registry))
            return self.docker_stub(command, **kwargs)

        with patch.object(MODULE, "docker", side_effect=docker) as run:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.remove(self.request("delete"))
        self.assertEqual(str(failure.exception), "RELEASE_IN_USE")
        self.assertFalse(any(call.args[0][:3] == ["docker", "image", "rm"] for call in run.call_args_list))
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

    def test_shared_image_container_is_preserved_when_deleting_unused_registration(self):
        self.catalog["releases"].append(dict(ENTRY, releaseId="44444444-4444-4444-8444-444444444444"))
        MODULE.CATALOG.write_text(json.dumps(self.catalog))

        def docker(command, **_kwargs):
            if command[:3] == ["docker", "container", "ls"]:
                return SimpleNamespace(returncode=0, stdout="a" * 64 + "\n")
            if command[:3] == ["docker", "container", "inspect"]:
                return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
            raise AssertionError("Shared image and its container must not be removed")

        with patch.object(MODULE, "docker", side_effect=docker):
            preview = MODULE.inspect_release(self.request())
            self.assertTrue(preview["allowed"])
            self.assertEqual(preview["containerReferences"], ["a" * 64])
            result = MODULE.remove(self.request("delete"))
        self.assertTrue(result["imageShared"])
        remaining = json.loads(MODULE.CATALOG.read_text())["releases"]
        self.assertEqual([item["releaseId"] for item in remaining], ["44444444-4444-4444-8444-444444444444"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
