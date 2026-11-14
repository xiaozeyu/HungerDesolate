"""Test gated Docker network and container creation commands."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instance_docker", PROJECT / "remote-scripts/actions/instance-docker.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
PROJECT_NAME = "fh_22222222222242228222222222222222"
RELEASE = "44444444-4444-4444-8444-444444444444"
OTHER_RELEASE = "55555555-5555-4555-8555-555555555555"


class DockerTests(unittest.TestCase):
    def test_existing_network_requires_matching_labels(self):
        labels = '{"com.hungerdesolate.schemaVersion":"1","com.hungerdesolate.hostId":"' + HOST + '","com.hungerdesolate.instanceId":"' + INSTANCE + '","com.hungerdesolate.role":"network"}'
        with patch.object(MODULE, "run", return_value=type("Result", (), {"returncode": 0, "stdout": labels})()):
            self.assertEqual(MODULE.ensure_network(PROJECT_NAME, HOST, INSTANCE), PROJECT_NAME + "_instance")
        wrong = labels.replace(INSTANCE, "33333333-3333-4333-8333-333333333333")
        with patch.object(MODULE, "run", return_value=type("Result", (), {"returncode": 0, "stdout": wrong})()):
            with self.assertRaises(MODULE.DockerRejected) as failure:
                MODULE.ensure_network(PROJECT_NAME, HOST, INSTANCE)
        self.assertEqual(str(failure.exception), "CREATE_NETWORK_CONFLICT")

    def test_missing_network_is_created_with_identity_labels(self):
        calls = []
        def command(args, **_kwargs):
            calls.append(args)
            if args[:3] == ["docker", "network", "inspect"]:
                return type("Result", (), {"returncode": 1, "stdout": ""})()
            return type("Result", (), {"returncode": 0, "stdout": "network-id"})()
        with patch.object(MODULE, "run", side_effect=command):
            self.assertEqual(MODULE.ensure_network(PROJECT_NAME, HOST, INSTANCE), PROJECT_NAME + "_instance")
        create = calls[-1]
        self.assertEqual(create[:4], ["docker", "network", "create", "--driver"])
        self.assertIn("com.hungerdesolate.instanceId=" + INSTANCE, create)

    def test_container_create_is_no_start_and_rejects_external_paths(self):
        calls = []
        def command(args, **_kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 0, "stdout": ""})()
        with patch.object(MODULE, "run", side_effect=command):
            result = MODULE.create_containers("/var/lib/famine-hub/instances/x/staging/t/compose.yaml", PROJECT_NAME)
        self.assertEqual(result, {"project": PROJECT_NAME, "created": True, "started": False})
        self.assertEqual(calls[0][-1], "create")
        self.assertEqual(calls[0], ["docker", "compose", "-p", PROJECT_NAME, "-f",
                                  "/var/lib/famine-hub/instances/x/staging/t/compose.yaml", "create"])
        self.assertNotIn("--no-start", calls[0])
        with self.assertRaises(MODULE.DockerRejected):
            MODULE.create_containers("/tmp/compose.yaml", PROJECT_NAME)

    def test_container_create_preserves_bounded_docker_diagnostic(self):
        diagnostic = "failed to bind host port 11000: address already in use"
        with patch.object(MODULE, "run", return_value=type(
                "Result", (), {"returncode": 1, "stdout": "", "stderr": diagnostic})()):
            with self.assertRaises(MODULE.DockerRejected) as failure:
                MODULE.create_containers("/var/lib/famine-hub/instances/x/staging/t/compose.yaml", PROJECT_NAME)
        self.assertEqual(str(failure.exception), "CREATE_CONTAINERS_FAILED")
        self.assertEqual(failure.exception.diagnostic, diagnostic)

    def test_diagnostics_remove_control_characters_and_are_bounded(self):
        self.assertEqual(MODULE.diagnostic_text("bad\x1b[31m compose\x1b[0m\n"), "bad compose")
        self.assertEqual(len(MODULE.diagnostic_text("x" * 3000)), 2000)

    def test_verification_requires_identity_labels_and_expected_container_roles(self):
        ids = "a" * 64 + "\n" + "b" * 64 + "\n"
        labels = '{"com.hungerdesolate.schemaVersion":"1","com.hungerdesolate.hostId":"' + HOST + '","com.hungerdesolate.instanceId":"' + INSTANCE + '","com.hungerdesolate.role":"master"}'
        caves = labels.replace('"master"', '"caves"')
        mounts = '[{"Source":"/var/lib/famine-hub/instances/' + INSTANCE + '/config"}]'
        bindings = '{}'
        def command(args, **_kwargs):
            if args[:3] == ["docker", "container", "ls"]:
                return type("Result", (), {"returncode": 0, "stdout": ids})()
            return type("Result", (), {"returncode": 0, "stdout": labels + "\t" + mounts + "\t" + bindings + "\n" + caves + "\t" + mounts + "\t" + bindings + "\n"})()
        with patch.object(MODULE, "run", side_effect=command):
            result = MODULE.verify_containers(PROJECT_NAME, HOST, INSTANCE, {"cavesEnabled": True})
        self.assertEqual(result["roles"], ["caves", "master"])

    def _verify_mounts(self, mounts, dst_release_id=RELEASE):
        ids = "a" * 64 + "\n"
        labels = '{"com.hungerdesolate.schemaVersion":"1","com.hungerdesolate.hostId":"' + HOST + '","com.hungerdesolate.instanceId":"' + INSTANCE + '","com.hungerdesolate.role":"master"}'
        def command(args, **_kwargs):
            if args[:3] == ["docker", "container", "ls"]:
                return type("Result", (), {"returncode": 0, "stdout": ids})()
            return type("Result", (), {"returncode": 0, "stdout": labels + "\t" + json.dumps(mounts) + "\t{}\n"})()
        with patch.object(MODULE, "run", side_effect=command):
            return MODULE.verify_containers(PROJECT_NAME, HOST, INSTANCE, {"cavesEnabled": False},
                                            dst_release_id=dst_release_id)

    def test_verification_accepts_the_release_shared_dst_mount_read_only(self):
        mounts = [{"Type": "bind", "Source": "/var/lib/famine-hub/instances/" + INSTANCE + "/config", "RW": True},
                  {"Type": "bind", "Source": "/var/lib/famine-hub/dst/" + RELEASE, "RW": False}]
        self.assertEqual(self._verify_mounts(mounts)["roles"], ["master"])

    def test_verification_rejects_other_external_or_writable_dst_mounts(self):
        base = {"Type": "bind", "Source": "/var/lib/famine-hub/instances/" + INSTANCE + "/config", "RW": True}
        for shared in ({"Type": "bind", "Source": "/var/lib/famine-hub/dst/" + RELEASE, "RW": True},
                       {"Type": "bind", "Source": "/var/lib/famine-hub/dst/" + OTHER_RELEASE, "RW": False},
                       {"Type": "bind", "Source": "/var/lib/famine-hub/dst", "RW": False},
                       {"Type": "volume", "Source": "/var/lib/famine-hub/dst/" + RELEASE, "RW": False}):
            with self.subTest(shared=shared), self.assertRaises(MODULE.DockerRejected) as failure:
                self._verify_mounts([base, shared])
            self.assertEqual(str(failure.exception), "CREATE_CONTAINER_MOUNT_INVALID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
