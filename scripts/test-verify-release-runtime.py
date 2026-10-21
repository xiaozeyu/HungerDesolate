"""Test temporary DST runtime verification and evidence publication."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import contextlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_runtime", PROJECT / "remote-scripts/actions/verify-release-runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
RELEASE_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "33333333-3333-4333-8333-333333333333"
DIGEST = "sha256:" + "a" * 64
SERVER = "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"
RELEASE = {
    "releaseId": RELEASE_ID, "platform": "linux/amd64", "image": "hungerdesolate/dst-runtime",
    "imageDigest": DIGEST, "dstBuild": "build-1", "artifactDigest": DIGEST,
    "scriptVersion": "V2-test", "verified": True, "runtimeVerified": False,
    "commands": {"master": [SERVER, "-console", "-shard", "Master"],
                 "caves": [SERVER, "-console", "-shard", "Caves"]},
}


def settings_bundle():
    files = {
        "Cluster_1/cluster.ini": "[NETWORK]\nlan_only_cluster=false\n[SHARD]\nbind_ip=127.0.0.1\nmaster_ip=127.0.0.1\nmaster_port=10889\n",
        "Cluster_1/cluster_token.txt": "test-token-do-not-persist\n",
        "Cluster_1/Master/server.ini": "[NETWORK]\nserver_port=11000\n[STEAM]\nauthentication_port=8766\nmaster_server_port=27016\n",
        "Cluster_1/Caves/server.ini": "[NETWORK]\nserver_port=11001\n[STEAM]\nauthentication_port=8767\nmaster_server_port=27017\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class RuntimeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-runtime-")
        self.root = Path(self.temp.name) / "famine-hub"
        stage = self.root / "staging" / RELEASE_ID
        stage.mkdir(parents=True, mode=0o700)
        (self.root / "releases").mkdir(mode=0o700)
        (self.root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (self.root / "host.json").chmod(0o600)
        (self.root / "releases" / "catalog.json").write_text(json.dumps({
            "schemaVersion": 1, "hostId": HOST, "releases": [RELEASE]
        }))
        (self.root / "releases" / "catalog.json").chmod(0o600)
        (stage / "release-metadata.json").write_text(json.dumps({"schemaVersion": 1, **RELEASE}))
        (stage / "release-metadata.json").chmod(0o600)
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
            patch.object(MODULE, "CATALOG", self.root / "releases" / "catalog.json"),
            patch.object(MODULE, "RUNTIME_LOCK", self.root / "releases" / "runtime-verify.lock"),
            patch.object(MODULE, "TEMPORARY_ROOT", Path(self.temp.name) / "run" / "release-verify"),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(MODULE.os, "chown", return_value=None),
            patch.object(Path, "lstat", root_owned),
            patch.object(MODULE.os, "fstat", root_fstat),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def docker_stub(self, command, **_kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=DIGEST + "\n")
        if command[:3] == ["docker", "network", "create"]:
            return SimpleNamespace(returncode=0, stdout="network-id\n")
        if command[:4] == ["docker", "container", "ls", "--all"]:
            return SimpleNamespace(returncode=0, stdout="f" * 64 + "\n")
        if command[:3] == ["docker", "network", "ls"]:
            return SimpleNamespace(returncode=0, stdout="e" * 64 + "\n")
        if command[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="container-id\n")
        if command[:2] == ["docker", "inspect"]:
            output = "true\n" if "State.Running" in command else "container-id\n"
            return SimpleNamespace(returncode=0, stdout=output)
        if command[:2] == ["docker", "top"]:
            raise AssertionError("host docker top is not a valid readiness probe")
        if command[:2] == ["docker", "exec"]:
            if "readlink" in command[-1]:
                return SimpleNamespace(returncode=0, stdout="DST_RUNNING")
            ports = (10889, 11000, 27016, 11001, 27017)
            rows = ["sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode"]
            rows.extend("%s: %08X:%04X 00000000:0000 07 00000000:00000000 00:00000000 00000000 1000 0 1" % (index, 0, port)
                        for index, port in enumerate(ports, 1))
            return SimpleNamespace(returncode=0, stdout="\n".join(rows) + "\n")
        return SimpleNamespace(returncode=0, stdout="")

    def test_runtime_verification_uses_isolated_containers_and_writes_secret_free_evidence(self):
        with patch.object(MODULE, "docker", side_effect=self.docker_stub), \
             patch.object(MODULE, "process_running", return_value=True), \
             patch.object(MODULE, "udp_ports", return_value={10889, 11000, 27016, 11001, 27017}):
            result = MODULE.verify({"hostId": HOST, "releaseId": RELEASE_ID,
                                    "catalogDigest": MODULE.catalog_digest(json.loads(MODULE.CATALOG.read_text())),
                                    "operationId": OPERATION_ID,
                                    "settingsBundleBase64": settings_bundle()})

        self.assertTrue(result["runtimeVerified"])
        self.assertEqual(result["listeners"], {"master": [10889, 11000, 27016], "caves": [11001, 27017]})
        evidence_path = self.root / "staging" / RELEASE_ID / "runtime.json"
        evidence = json.loads(evidence_path.read_text())
        self.assertEqual(evidence["imageDigest"], DIGEST)
        self.assertTrue(evidence["lanOnly"])
        self.assertNotIn("test-token-do-not-persist", evidence_path.read_text())
        self.assertFalse(any((Path(self.temp.name) / "run" / "release-verify").iterdir()))

    def test_listener_timeout_returns_port_diagnostics_and_cleans_up(self):
        with patch.object(MODULE, "docker", side_effect=self.docker_stub), \
             patch.object(MODULE, "process_running", return_value=True), \
             patch.object(MODULE, "udp_ports", return_value=set()), \
             patch.object(MODULE.time, "monotonic", side_effect=[0, 121]):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.verify({"hostId": HOST, "releaseId": RELEASE_ID,
                               "catalogDigest": MODULE.catalog_digest(json.loads(MODULE.CATALOG.read_text())),
                               "operationId": OPERATION_ID,
                               "settingsBundleBase64": settings_bundle()})
        self.assertEqual(str(failure.exception), "RELEASE_RUNTIME_LISTENERS_NOT_READY")
        self.assertEqual(failure.exception.details["masterMissingPorts"], [10889, 11000, 27016])
        self.assertEqual(failure.exception.details["cavesMissingPorts"], [11001, 27017])
        self.assertFalse(any((Path(self.temp.name) / "run" / "release-verify").iterdir()))

    def test_rejects_zip_path_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../cluster.ini", "unsafe")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        destination = Path(self.temp.name) / "unsafe"
        with self.assertRaises(MODULE.Rejected):
            MODULE.prepare_bundle(encoded, destination)
        self.assertFalse(destination.exists())

    def test_reject_can_attach_safe_listener_port_diagnostics(self):
        details = {"masterMissingPorts": [11000], "cavesMissingPorts": [11001]}
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.reject("RELEASE_RUNTIME_LISTENERS_NOT_READY", details)
        self.assertEqual(str(failure.exception), "RELEASE_RUNTIME_LISTENERS_NOT_READY")
        self.assertEqual(failure.exception.details, details)

    def test_bundle_rebinds_cluster_shards_to_isolated_container_network(self):
        destination = Path(self.temp.name) / "normalized" / "Cluster_1"
        MODULE.prepare_bundle(settings_bundle(), destination)
        parser = MODULE.configparser.ConfigParser(interpolation=None)
        parser.read(destination / "cluster.ini", encoding="utf-8")
        self.assertEqual(parser.get("SHARD", "bind_ip"), "0.0.0.0")
        self.assertEqual(parser.get("SHARD", "master_ip"), "master")

    def test_process_and_listener_probes_require_the_dst_process_and_udp_sockets(self):
        responses = [SimpleNamespace(returncode=0, stdout="true\n"),
                     SimpleNamespace(returncode=0, stdout="DST_RUNNING\n")]
        with patch.object(MODULE, "docker", side_effect=responses):
            self.assertTrue(MODULE.process_running("master"))
        udp_data = "header\n1: 00000000:2AF8 00000000:0000 07\n"
        with patch.object(MODULE, "docker", return_value=SimpleNamespace(returncode=0, stdout=udp_data)):
            self.assertEqual(MODULE.udp_ports("master"), {11000})

    def test_main_returns_a_safe_json_envelope_for_unexpected_errors(self):
        output = io.StringIO()
        with patch.object(MODULE, "verify", side_effect=RuntimeError("private diagnostic")), \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            MODULE.main({})
        response = json.loads(output.getvalue())
        self.assertEqual(response["ok"], False)
        self.assertEqual(response["code"], "RELEASE_RUNTIME_VERIFY_FAILED")
        self.assertEqual(response["details"]["failureType"], "RuntimeError")
        self.assertEqual(response["details"]["failureStage"], "initialize")
        self.assertRegex(response["details"]["failureFunction"], r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
        self.assertGreaterEqual(response["details"]["failureLine"], 1)
        self.assertNotIn("private diagnostic", output.getvalue())

    def test_stdin_wrapper_can_invoke_script_without_import_errors(self):
        script = (PROJECT / "remote-scripts/actions/verify-release-runtime.py").read_text()
        source = "import base64\n" + script + "\nmain(json.loads(base64.b64decode('e30=').decode('utf-8')))\n"
        result = subprocess.run(
            ["python3", "-I", "-"], input=source, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "CREATE_RELEASE_INPUT_INVALID",
                                                    "details": {"failureStage": "validateRequest"}})
        self.assertNotIn("NameError", result.stderr)

    def test_runtime_mounts_config_under_persistent_storage_root(self):
        source = (PROJECT / "remote-scripts/actions/verify-release-runtime.py").read_text()
        self.assertIn('dst=/srv/data/config,readonly', source)
        self.assertNotIn('dst=/srv/config,readonly', source)
        self.assertLess(source.index('dst=/srv/data"'), source.index('dst=/srv/data/config,readonly'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
