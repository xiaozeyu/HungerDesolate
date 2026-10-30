"""Test the Docker install preflight without network or system changes."""
import importlib.util
import json
import os
import io
import socket
import ssl
import http.client
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("docker_install_preview", PROJECT / "remote-scripts/probes/docker-install-preview.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self, _limit):
        return b"-----BEGIN PGP SIGNED MESSAGE-----\nfixture"


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = __import__("tempfile").TemporaryDirectory(prefix="docker-preview-")
        root = Path(self.temp.name)
        MODULE.ROOT = root / "famine-hub"
        MODULE.ROOT.mkdir(mode=0o700)
        host_id="11111111-1111-4111-8111-111111111111"
        (MODULE.ROOT/"host.json").write_text(json.dumps({"schemaVersion":1,"hostId":host_id}))
        (MODULE.ROOT/"registry.json").write_text(json.dumps({"schemaVersion":1,"hostId":host_id,"registryRevision":0,"instances":[]}))
        MODULE.BOOTSTRAP = root / "famine-hub.bootstrap"
        MODULE.SOURCE_FILE = root / "docker.sources"
        MODULE.LEGACY_SOURCE_FILE = root / "docker.list"
        MODULE.KEY_FILE = root / "docker.asc"
        MODULE.SOURCES_DIR = root / "sources.list.d"
        MODULE.SOURCES_DIR.mkdir(mode=0o755)
        MODULE.APT_SOURCE_LIST = root / "sources.list"
        MODULE.RELEASE_FILE = root / "os-release"
        MODULE.RELEASE_FILE.write_text('ID=ubuntu\nVERSION_ID="26.04"\nVERSION_CODENAME=resolute\n')
        MODULE.SYSTEMD_DIR = root / "systemd"
        MODULE.SYSTEMD_DIR.mkdir()
        def run_command(command,**_kwargs):
            if command and command[0]=="dpkg":
                return SimpleNamespace(returncode=0,stdout="")
            return SimpleNamespace(returncode=1,stdout="")
        self.patches = [
            patch.object(MODULE.platform, "machine", return_value="x86_64"),
            patch.object(MODULE.shutil, "which", side_effect=lambda name: None if name == "docker" else f"/usr/bin/{name}"),
            patch.object(MODULE.shutil, "disk_usage", return_value=SimpleNamespace(free=8 * 1024**3)),
            patch.object(MODULE.subprocess, "run", side_effect=run_command),
            patch.object(MODULE.urllib.request, "urlopen", return_value=Response()),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "ready path requires root-owned fixture files")
    def test_supported_root_ubuntu_is_ready_and_read_only(self):
        result = MODULE.inspect()
        self.assertEqual(result["status"], "ready", result["blockers"])
        self.assertEqual(result["distribution"], {"id":"ubuntu","version":"26.04","codename":"resolute","architecture":"x86_64"})
        self.assertEqual(result["packages"], MODULE.PACKAGES)
        self.assertFalse(MODULE.SOURCE_FILE.exists())
        self.assertFalse(MODULE.KEY_FILE.exists())

    def test_unsupported_os_nonroot_conflicts_and_offline_block(self):
        MODULE.RELEASE_FILE.write_text("ID=debian\nVERSION_ID=13\nVERSION_CODENAME=trixie\n")
        with patch.object(MODULE.os, "geteuid", return_value=1000):
            with patch.object(MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="docker.io\tinstalled\n")):
                with patch.object(MODULE.urllib.request, "urlopen", side_effect=TimeoutError):
                    result = MODULE.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue({"UNSUPPORTED_DISTRIBUTION","ROOT_REQUIRED","CONFLICTING_PACKAGES","DOCKER_REPOSITORY_TIMEOUT"}.issubset(result["blockers"]))

    def test_existing_repository_is_never_overwritten(self):
        MODULE.SOURCE_FILE.write_text("Types: deb\nURIs: https://other.invalid/\n")
        result = MODULE.inspect()
        self.assertIn("DOCKER_SOURCE_CONFLICT", result["blockers"])

    def test_apt_state_is_returned_to_the_desktop(self):
        for running, clean, blocker in [(False, True, None), (True, True, "APT_BUSY"), (False, False, "DPKG_STATE_INCOMPLETE")]:
            with patch.object(MODULE, "apt_state", return_value=(running, clean)):
                result = MODULE.inspect()
            self.assertEqual(result["aptRunning"], running)
            self.assertEqual(result["dpkgClean"], clean)
            if blocker:
                self.assertIn(blocker, result["blockers"])

    def test_repository_failures_are_specific_and_do_not_leak_remote_text(self):
        private_text = "sensitive-proxy-response"
        cases = [
            (TimeoutError(private_text), "TIMEOUT"),
            (MODULE.urllib.error.URLError(TimeoutError(private_text)), "TIMEOUT"),
            (MODULE.urllib.error.URLError(socket.gaierror(-2, private_text)), "DNS_FAILED"),
            (MODULE.urllib.error.URLError(ssl.SSLCertVerificationError(1, private_text)), "TLS_CERT_INVALID"),
            (ssl.SSLError(1, private_text), "TLS_FAILED"),
            (ConnectionRefusedError(private_text), "CONNECTION_FAILED"),
            (http.client.IncompleteRead(b"partial"), "RESPONSE_INVALID"),
        ]
        for cause, suffix in cases:
            with self.subTest(suffix=suffix):
                with patch.object(MODULE.urllib.request, "urlopen", side_effect=cause):
                    result = MODULE.inspect()
                self.assertFalse(result["repositoryReachable"])
                self.assertEqual([code for code in result["blockers"] if code.startswith("DOCKER_REPOSITORY_")], ["DOCKER_REPOSITORY_" + suffix])
                self.assertNotIn(private_text, json.dumps(result))

    def test_repository_http_status_and_response_validation(self):
        for status in (403, 404, 407, 429, 500, 503):
            body = io.BytesIO(b"private body")
            cause = MODULE.urllib.error.HTTPError("https://example.invalid", status, "private message", {}, body)
            with patch.object(MODULE.urllib.request, "urlopen", side_effect=cause):
                self.assertEqual(MODULE.check_repository(), "DOCKER_REPOSITORY_HTTP_" + str(status))
            self.assertTrue(body.closed)
        with patch.object(Response, "read", return_value=b"<html>proxy page</html>"):
            self.assertEqual(MODULE.check_repository(), "DOCKER_REPOSITORY_RESPONSE_INVALID")
        for status in (200, 206):
            with patch.object(Response, "status", status):
                self.assertIsNone(MODULE.check_repository())

    def test_repository_result_remains_a_snapshot_without_background_retry(self):
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=TimeoutError) as request:
            first = MODULE.inspect()
            self.assertEqual(request.call_count, 1)
        second = MODULE.inspect()
        self.assertFalse(first["repositoryReachable"])
        self.assertTrue(second["repositoryReachable"])
        self.assertIn("DOCKER_REPOSITORY_TIMEOUT", first["blockers"])
        self.assertNotIn("DOCKER_REPOSITORY_TIMEOUT", second["blockers"])

    def test_transient_network_result_does_not_change_local_snapshot(self):
        available = MODULE.inspect()
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=ConnectionResetError("private network text")):
            unavailable = MODULE.inspect()
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=AssertionError("unexpected network recheck")) as request:
            local = MODULE.inspect(check_repository_access=False)
        request.assert_not_called()
        self.assertTrue(available["repositoryReachable"])
        self.assertFalse(unavailable["repositoryReachable"])
        self.assertIsNone(local["repositoryReachable"])
        self.assertEqual(available["snapshotDigest"], unavailable["snapshotDigest"])
        self.assertEqual(available["snapshotDigest"], local["snapshotDigest"])
        self.assertIn("DOCKER_REPOSITORY_CONNECTION_FAILED", unavailable["blockers"])
        self.assertFalse(any(code.startswith("DOCKER_REPOSITORY_") for code in local["blockers"]))
        self.assertNotIn("private network text", json.dumps(unavailable))

    def test_local_recheck_still_blocks_changed_repository_configuration(self):
        MODULE.SOURCE_FILE.write_text("Types: deb\nURIs: https://other.invalid/\n")
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=AssertionError("unexpected network recheck")) as request:
            current = MODULE.inspect(check_repository_access=False)
        request.assert_not_called()
        self.assertEqual(current["status"], "blocked")
        self.assertIn("DOCKER_SOURCE_CONFLICT", current["blockers"])
        # Matching a digest alone must never waive the blockers check.
        self.assertIsNone(current["repositoryReachable"])

    def test_local_recheck_detects_package_state_changes_in_snapshot(self):
        before = MODULE.inspect()
        with patch.object(MODULE, "installed_docker_packages", return_value={"docker-ce": "changed-version"}):
            changed = MODULE.inspect(check_repository_access=False)
        self.assertNotEqual(before["snapshotDigest"], changed["snapshotDigest"])

    def test_remote_controller_bundle_compiles_with_fixed_worker_sources(self):
        control=(PROJECT/"remote-scripts/actions/docker-install-control.py").read_text()
        worker=(PROJECT/"remote-scripts/actions/docker-install-worker.py").read_text()
        preflight=(PROJECT/"remote-scripts/probes/docker-install-preview.py").read_text()
        bundled=(preflight+"\n"+control+"\nPREFLIGHT_SOURCE="+json.dumps(preflight)
                +"\nWORKER_SOURCE="+json.dumps(worker)
                +"\nmain(json.loads(base64.b64decode('e30=').decode('utf-8')))\n")
        compile(bundled,"<docker-install-controller>","exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
