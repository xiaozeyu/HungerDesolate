import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


def load_probe():
    path = Path(__file__).parents[1] / "remote-scripts" / "probes" / "instance-mods.py"
    spec = importlib.util.spec_from_file_location("instance_mods_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstanceModsProbeTests(unittest.TestCase):
    def setUp(self):
        self.probe = load_probe()
        self.root = Path(tempfile.mkdtemp())
        self.probe.ROOT = self.root
        self.host_id = "11111111-1111-4111-8111-111111111111"
        self.instance_id = "22222222-2222-4222-8222-222222222222"
        self.instance = self.root / "instances" / self.instance_id
        (self.instance / "desired").mkdir(parents=True)
        (self.instance / "mods" / "releases" / "mods-1" / "Master" / "workshop-123").mkdir(parents=True)
        manifest = self.instance / "manifest.json"
        manifest.write_text(json.dumps({
            "schemaVersion": 1,
            "hostId": self.host_id,
            "instanceId": self.instance_id,
            "configRevision": 3,
        }))
        manifest.chmod(0o600)
        active = self.instance / "active-release.json"
        active.write_text(json.dumps({"modsReleaseId": "mods-1"}))
        active.chmod(0o600)
        desired = self.instance / "desired" / "mods.json"
        desired.write_text(json.dumps({
            "schemaVersion": 1,
            "instanceId": self.instance_id,
            "revision": 3,
            "mods": [{"workshopId": "123", "enabled": True, "configurationOptions": {"mode": 2}}],
        }))
        desired.chmod(0o600)
        modinfo = self.instance / "mods" / "releases" / "mods-1" / "Master" / "workshop-123" / "modinfo.lua"
        modinfo.write_text(
            "configuration_options = {{ name = 'mode', default = 2 }}\n"
        )
        modinfo.chmod(0o600)

    def request(self):
        return {
            "schemaVersion": 1,
            "requestId": "33333333-3333-4333-8333-333333333333",
            "serverId": "44444444-4444-4444-8444-444444444444",
            "hostId": self.host_id,
            "instanceId": self.instance_id,
        }

    def test_reads_desired_state_and_modinfo_source(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.probe.main(self.request())
        response = json.loads(output.getvalue())
        self.assertEqual(response["configRevision"], 3)
        self.assertEqual(response["items"][0]["workshopId"], "123")
        self.assertEqual(response["items"][0]["desired"]["enabled"], True)
        self.assertIsNotNone(response["items"][0]["artifact"]["sourceBase64"])

    def test_exec_namespace_does_not_run_stdin_entrypoint(self):
        source_path = Path(__file__).parents[1] / "remote-scripts" / "probes" / "instance-mods.py"
        namespace = {"__name__": "instance_mods_probe"}
        exec(compile(source_path.read_text(), str(source_path), "exec"), namespace)
        namespace["ROOT"] = self.root
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace["main"](self.request())
        self.assertEqual(json.loads(output.getvalue())["configRevision"], 3)

    def test_rejects_revision_that_does_not_match_manifest(self):
        desired = json.loads((self.instance / "desired" / "mods.json").read_text())
        desired["revision"] = 4
        path = self.instance / "desired" / "mods.json"
        path.write_text(json.dumps(desired))
        path.chmod(0o600)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(self.probe.ModProbeError) as failure:
            self.probe.main(self.request())
        self.assertEqual(failure.exception.code, "MOD_STATE_REVISION_INVALID")

    def test_reads_pinned_prepared_workshop_artifact(self):
        digest = "a" * 64
        desired_path = self.instance / "desired" / "mods.json"
        desired = json.loads(desired_path.read_text())
        desired["mods"][0]["artifactDigest"] = digest
        desired_path.write_text(json.dumps(desired)); desired_path.chmod(0o600)
        artifact = self.instance / "mods" / "prepared" / "123" / digest
        content = artifact / "content"
        content.mkdir(parents=True, mode=0o700)
        metadata = {"schemaVersion": 1, "workshopId": "123", "contentDigest": digest,
                    "preparedAt": "2026-10-22T00:00:00Z", "fileCount": 1, "sizeBytes": 45}
        manifest = artifact / "manifest.json"
        manifest.write_text(json.dumps(metadata)); manifest.chmod(0o600)
        modinfo = content / "modinfo.lua"
        modinfo.write_text("configuration_options = {}\n"); modinfo.chmod(0o444)
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.probe.main(self.request())
        result = json.loads(output.getvalue())["items"][0]["artifact"]
        self.assertEqual(result["contentDigest"], digest)
        self.assertEqual(result["preparedAt"], "2026-10-22T00:00:00Z")
        self.assertEqual(result["folderName"], "workshop-123")

    def test_reads_candidate_artifact_without_replacing_desired_artifact(self):
        desired_digest = "a" * 64
        candidate_digest = "b" * 64
        desired_path = self.instance / "desired" / "mods.json"
        desired = json.loads(desired_path.read_text())
        desired["mods"][0]["artifactDigest"] = desired_digest
        desired_path.write_text(json.dumps(desired)); desired_path.chmod(0o600)
        prepared_root = self.instance / "mods" / "prepared" / "123"
        for digest, version in ((desired_digest, "v1.0.0"), (candidate_digest, "v1.1.0")):
            artifact = prepared_root / digest
            content = artifact / "content"
            content.mkdir(parents=True, mode=0o700)
            manifest = artifact / "manifest.json"
            manifest.write_text(json.dumps({"schemaVersion": 1, "workshopId": "123",
                "contentDigest": digest, "preparedAt": "2026-10-23T00:00:00Z",
                "sourceHandle": "200" if digest == candidate_digest else "100"}))
            manifest.chmod(0o600)
            (content / "modinfo.lua").write_text(f"version = '{version}'\nconfiguration_options = {{}}\n")
            (content / "modinfo.lua").chmod(0o444)
        candidates = self.instance / "mods" / "candidates"
        candidates.mkdir(mode=0o700)
        pointer = candidates / "123.json"
        pointer.write_text(json.dumps({"schemaVersion": 1, "workshopId": "123",
            "contentDigest": candidate_digest, "sourceHandle": "200",
            "preparedAt": "2026-10-23T00:00:00Z"}))
        pointer.chmod(0o600)
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.probe.main(self.request())
        item = json.loads(output.getvalue())["items"][0]
        self.assertEqual(item["artifact"]["contentDigest"], desired_digest)
        self.assertEqual(item["candidateArtifact"]["contentDigest"], candidate_digest)
        self.assertEqual(item["candidateArtifact"]["sourceHandle"], "200")

    def test_missing_pinned_digest_does_not_fall_back_to_another_release(self):
        desired_path = self.instance / "desired" / "mods.json"
        desired = json.loads(desired_path.read_text())
        desired["mods"][0]["artifactDigest"] = "f" * 64
        desired_path.write_text(json.dumps(desired)); desired_path.chmod(0o600)
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.probe.main(self.request())
        self.assertIsNone(json.loads(output.getvalue())["items"][0]["artifact"])

    def test_rejects_symlinked_prepared_content_directory(self):
        digest = "a" * 64
        desired_path = self.instance / "desired" / "mods.json"
        desired = json.loads(desired_path.read_text())
        desired["mods"][0]["artifactDigest"] = digest
        desired_path.write_text(json.dumps(desired)); desired_path.chmod(0o600)
        prepared = self.instance / "mods" / "prepared" / "123"
        prepared.mkdir(parents=True)
        artifact = prepared / digest
        artifact.mkdir()
        (artifact / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "workshopId": "123", "contentDigest": digest}))
        (artifact / "manifest.json").chmod(0o600)
        content = self.root / "outside"
        content.mkdir()
        (content / "modinfo.lua").write_text("configuration_options = {}\n")
        artifact.joinpath("content").symlink_to(content, target_is_directory=True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(self.probe.ModProbeError) as failure:
            self.probe.main(self.request())
        self.assertEqual(failure.exception.code, "MOD_ARTIFACT_INVALID")

    def test_rejects_group_writable_prepared_parent(self):
        prepared = self.instance / "mods" / "prepared"
        prepared.mkdir(parents=True)
        prepared.chmod(0o770)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(self.probe.ModProbeError) as failure:
            self.probe.main(self.request())
        self.assertEqual(failure.exception.code, "MOD_ARTIFACT_INVALID")


if __name__ == "__main__":
    unittest.main()
