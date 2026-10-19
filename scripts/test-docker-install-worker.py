"""Test installer task state and apt removal guard without touching the host."""
import importlib.util
from contextlib import contextmanager
import errno
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("docker_install_worker",ROOT/"remote-scripts/actions/docker-install-worker.py")
WORKER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)

KEY = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfixture\n-----END PGP PUBLIC KEY BLOCK-----\n"


class KeyResponse(io.BytesIO):
    status = 200


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="docker-install-worker-")
        self.task=Path(self.temp.name)/"11111111-1111-4111-8111-111111111111"
        self.task.mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def isolated_worker(self):
        root = Path(self.temp.name)
        host_id = "33333333-3333-4333-8333-333333333333"
        (self.task / "plan.json").write_text(json.dumps({"taskId":self.task.name, "hostId":host_id, "snapshotDigest":"a"*64}))
        (self.task / "status.json").write_text(json.dumps({"hostId":host_id, "sequence":1, "startedAtUnixSeconds":1700000000}))
        (self.task / "preflight.py").write_text("def inspect():\n    return " + repr({"status":"ready", "hostId":host_id, "snapshotDigest":"a"*64}) + "\n")
        original_stat, original_lstat, original_fstat = Path.stat, Path.lstat, os.fstat
        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        # Only root identity is simulated. All files stay inside the temporary fixture.
        with patch.dict(os.environ), patch.object(WORKER, "BOOTSTRAP", root), \
             patch.object(WORKER, "TASKS", root), patch.object(WORKER, "SOURCE_FILE", root / "apt" / "docker.sources"), \
             patch.object(WORKER, "KEY_FILE", root / "keyrings" / "docker.asc"), \
             patch.object(WORKER.os, "geteuid", return_value=0), \
             patch.object(Path, "stat", lambda path, **kw: root_owned(original_stat(path, **kw))), \
             patch.object(Path, "lstat", lambda path, **kw: root_owned(original_lstat(path, **kw))), \
             patch.object(WORKER.os, "fstat", lambda fd: root_owned(original_fstat(fd))), \
             patch.object(WORKER.time, "sleep"), \
             patch.object(WORKER.subprocess, "run") as command:
            # No apt/systemd/docker command is ever run by these tests.
            def complete(args, **_kwargs):
                if args[:2] == ["docker", "version"]:
                    output = "linux amd64 29.0.0"
                elif args[:3] == ["docker", "compose", "version"]:
                    output = "2.40.0"
                elif args[0] == "dpkg-query":
                    output = "\n".join(name + "\t1.0" for name in WORKER.PACKAGES)
                else:
                    output = b"Inst docker-ce (fixture)\n"
                return subprocess.CompletedProcess(args, 0, output)
            command.side_effect = complete
            yield command

    def test_full_worker_reaches_verified_with_real_temporary_files(self):
        with self.isolated_worker() as commands, \
             patch.object(WORKER.urllib.request, "urlopen", return_value=KeyResponse(KEY)):
            self.assertEqual(WORKER.main(str(self.task)), 0)
            self.assertEqual(WORKER.KEY_FILE.read_bytes(), KEY)
            self.assertEqual(WORKER.SOURCE_FILE.read_text(), WORKER.SOURCE)
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["status"], "succeeded")
            self.assertEqual(state["phase"], "verified")
            self.assertEqual(state["installedVersions"]["dockerEngine"], "29.0.0")
            self.assertIn(["systemctl", "enable", "--now", "docker.service"], [call.args[0] for call in commands.call_args_list])

    def test_full_worker_retains_download_failure_phase_and_safe_reason(self):
        with self.isolated_worker() as commands, \
             patch.object(WORKER.urllib.request, "urlopen", side_effect=TimeoutError("private token")) as download:
            self.assertEqual(WORKER.main(str(self.task)), 1)
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["phase"], "configuringRepository")
            self.assertEqual(state["messageCode"], "INSTALL_KEY_TIMEOUT")
            self.assertEqual(download.call_count, 3)
            commands.assert_not_called()
            self.assertFalse(WORKER.SOURCE_FILE.exists())
            self.assertNotIn("private token", json.dumps(state))

    def test_download_retries_transient_failure_then_succeeds(self):
        with patch.object(WORKER.urllib.request, "urlopen", side_effect=[TimeoutError(), KeyResponse(KEY)]) as download, \
             patch.object(WORKER.time, "sleep"):
            self.assertEqual(WORKER.download_signing_key(), KEY)
            self.assertEqual(download.call_count, 2)

    def test_key_download_classifies_network_errors_without_secret_text(self):
        cases = [
            (WORKER.urllib.error.URLError(socket.gaierror(-2, "secret")), "INSTALL_KEY_DNS_FAILED", 3),
            (WORKER.urllib.error.URLError(ssl.SSLCertVerificationError(1, "secret")), "INSTALL_KEY_TLS_CERT_INVALID", 1),
            (ssl.SSLError(1, "secret"), "INSTALL_KEY_TLS_FAILED", 1),
            (ConnectionRefusedError("secret"), "INSTALL_KEY_CONNECTION_FAILED", 3),
            (WORKER.urllib.error.HTTPError("https://example.invalid", 403, "secret", {}, None), "INSTALL_KEY_HTTP_403", 1),
            (WORKER.urllib.error.HTTPError("https://example.invalid", 503, "secret", {}, None), "INSTALL_KEY_HTTP_503", 3),
        ]
        for cause, code, attempts in cases:
            with self.subTest(code=code), patch.object(WORKER.urllib.request, "urlopen", side_effect=cause) as download, \
                 patch.object(WORKER.time, "sleep"):
                with self.assertRaises(RuntimeError) as failure:
                    WORKER.download_signing_key()
                self.assertEqual(str(failure.exception), code)
                self.assertEqual(download.call_count, attempts)

    def test_key_format_failure_and_existing_source_are_preserved(self):
        with self.isolated_worker(), patch.object(WORKER.urllib.request, "urlopen", return_value=KeyResponse(b"<html>error</html>")):
            WORKER.SOURCE_FILE.parent.mkdir()
            WORKER.SOURCE_FILE.write_text(WORKER.SOURCE)
            self.assertEqual(WORKER.main(str(self.task)), 1)
            self.assertEqual(WORKER.SOURCE_FILE.read_text(), WORKER.SOURCE)
            self.assertFalse(WORKER.KEY_FILE.exists())
            self.assertEqual(json.loads((self.task / "status.json").read_text())["messageCode"], "INSTALL_SIGNING_KEY_INVALID")

    def test_filesystem_errors_keep_failed_stage(self):
        for number, code in [(errno.ENOSPC,"INSTALL_DISK_FULL"), (errno.EACCES,"INSTALL_PERMISSION_DENIED"), (errno.EROFS,"INSTALL_FILESYSTEM_READ_ONLY")]:
            with self.subTest(code=code), self.isolated_worker(), \
                 patch.object(WORKER, "ensure_repository", side_effect=OSError(number, "secret path")):
                self.assertEqual(WORKER.main(str(self.task)), 1)
                state = json.loads((self.task / "status.json").read_text())
                self.assertEqual(state["phase"], "configuringRepository")
                self.assertEqual(state["messageCode"], code)
                self.assertNotIn("secret path", json.dumps(state))

    def test_invalid_plan_is_reported_during_recheck(self):
        with self.isolated_worker() as commands:
            (self.task / "plan.json").write_text("{broken")
            self.assertEqual(WORKER.main(str(self.task)), 1)
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["phase"], "rechecking")
            self.assertEqual(state["messageCode"], "INSTALL_TASK_DATA_INVALID")
            commands.assert_not_called()

    def test_command_failures_report_the_actual_stage(self):
        with self.isolated_worker() as commands, patch.object(WORKER, "ensure_repository"):
            commands.side_effect = FileNotFoundError(errno.ENOENT, "not found", "apt-get")
            self.assertEqual(WORKER.main(str(self.task)), 1)
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["phase"], "updatingPackageIndex")
            self.assertEqual(state["messageCode"], "INSTALL_COMMAND_MISSING")
        with self.isolated_worker() as commands, patch.object(WORKER, "ensure_repository"):
            commands.return_value = subprocess.CompletedProcess([], 100, b"private error")
            commands.side_effect = None
            self.assertEqual(WORKER.main(str(self.task)), 1)
            state = json.loads((self.task / "status.json").read_text())
            self.assertEqual(state["phase"], "updatingPackageIndex")
            self.assertEqual(state["messageCode"], "INSTALL_COMMAND_FAILED")

    def test_journal_sequence_advances_and_keeps_terminal_state(self):
        WORKER.write_status(self.task,"running","updatingPackageIndex")
        WORKER.write_status(self.task,"running","installingPackages")
        state=__import__("json").loads((self.task/"status.json").read_text())
        self.assertEqual(state["sequence"],2)
        self.assertEqual(state["phase"],"installingPackages")
        self.assertEqual((self.task/"status.json").stat().st_mode & 0o777,0o600)

    def test_apt_simulation_must_not_remove_packages(self):
        log=self.task/"apt.log"
        output=b"Inst docker-ce (fixture)\n"
        with patch.object(WORKER.subprocess,"run",return_value=subprocess.CompletedProcess([],0,output)):
            WORKER.simulate_install(log)
        self.assertIn(b"Inst docker-ce",log.read_bytes())
        with patch.object(WORKER.subprocess,"run",return_value=subprocess.CompletedProcess([],0,b"Remv docker.io [1.0]\n")):
            with self.assertRaisesRegex(RuntimeError,"INSTALL_WOULD_REMOVE_PACKAGES"):
                WORKER.simulate_install(log)

    def test_creation_never_overwrites_existing_apt_file(self):
        from pathlib import Path
        target=Path(self.temp.name)/"already-exists"
        target.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            WORKER.create_file(target,b"replace",0o600)
        self.assertEqual(target.read_bytes(),b"keep")

    def test_failed_key_download_does_not_publish_a_new_apt_source(self):
        source = Path(self.temp.name) / "apt" / "docker.sources"
        key = Path(self.temp.name) / "keyrings" / "docker.asc"
        with patch.object(WORKER, "SOURCE_FILE", source), patch.object(WORKER, "KEY_FILE", key), \
             patch.object(WORKER.urllib.request, "urlopen", side_effect=TimeoutError("private network error")), \
             patch.object(WORKER.time, "sleep"):
            with self.assertRaises((OSError, RuntimeError)):
                WORKER.ensure_repository()
        self.assertFalse(source.exists(), "a failed download must not leave a new APT source behind")
        self.assertFalse(key.exists())

    def test_runtime_verification_requires_linux_amd64_compose_v2_and_active_service(self):
        calls=[]
        def fake_run(command,**_kwargs):
            calls.append(command)
            if command[:2]==["docker","version"]:
                return subprocess.CompletedProcess(command,0,"linux amd64 29.0.0","")
            if command[:3]==["docker","compose","version"]:
                return subprocess.CompletedProcess(command,0,"2.40.0","")
            return subprocess.CompletedProcess(command,0,"active","")
        with patch.object(WORKER.subprocess,"run",side_effect=fake_run):
            verified=WORKER.verify_runtime(self.task/"runtime.log")
        self.assertEqual(verified,{"dockerEngine":"29.0.0","dockerCompose":"2.40.0"})
        self.assertIn(["systemctl","is-active","--quiet","docker.service"],calls)
        with patch.object(WORKER.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"2.0.0", "")):
            self.assertIsNone(WORKER.compose_major("unknown"))


if __name__=="__main__":
    unittest.main(verbosity=2)
