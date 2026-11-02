"""Test the managed DST start preflight and readiness checks."""
import importlib.util
import json
import os
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "instance_runtime_start", PROJECT / "remote-scripts/actions/instance-runtime-start.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HELPERS = {}
exec((PROJECT / "remote-scripts/actions/instance-release.py").read_text(), HELPERS)
MODULE.RELEASE_HELPERS = HELPERS
HOST = "11111111-1111-4111-8111-111111111111"
INSTANCE = "22222222-2222-4222-8222-222222222222"
RELEASE = "33333333-3333-4333-8333-333333333333"
REQUEST = {"schemaVersion": 1, "requestId": "44444444-4444-4444-8444-444444444444",
           "operationId": "55555555-5555-4555-8555-555555555555", "hostId": HOST,
           "instanceId": INSTANCE, "expectedRevision": 4}


class PlayerCountQueryTests(unittest.TestCase):
    HEADER = b"\xff\xff\xff\xff"
    PLAYER = HEADER + b"U\xff\xff\xff\xff"
    INFO = HEADER + b"TSource Engine Query\x00"

    def info(self, count):
        return self.HEADER + b"I\x11test\x00forest\x00dst\x00DST\x00" + (322330 & 65535).to_bytes(2, "little") + bytes([count, 6, 0])

    def players(self, count):
        return self.HEADER + b"D" + bytes([count]) + b"".join(
            bytes([index]) + b"player\x00" + b"\x00" * 8 for index in range(count))

    def query(self, responses, address="127.0.0.1"):
        sockets = []
        packets = []
        pending = iter(responses)

        def receive(_limit):
            reply = next(pending)
            if isinstance(reply, Exception):
                raise reply
            return reply, (address, 27016)

        def create(*_args):
            sock = MagicMock()
            sock.recvfrom.side_effect = receive
            sock.sendto.side_effect = lambda packet, address: packets.append((packet, address))
            sockets.append(sock)
            return sock

        with patch.object(MODULE.socket, "socket", side_effect=create):
            result = MODULE.a2s_player_count(27016, address)
        for sock in sockets:
            sock.close.assert_called_once()
        return result, packets

    def test_player_query_timeout_still_queries_info(self):
        result, packets = self.query([TimeoutError(), self.info(3)])
        self.assertEqual(result, 3)
        self.assertEqual([packet for packet, _address in packets], [self.PLAYER, self.INFO])

    def test_challenged_player_query_timeout_still_queries_info(self):
        result, packets = self.query([self.HEADER + b"A1234", TimeoutError(), self.info(2)])
        self.assertEqual(result, 2)
        self.assertEqual([packet for packet, _address in packets],
                         [self.PLAYER, self.HEADER + b"U1234", self.INFO])

    def test_info_fallback_handles_its_own_challenge(self):
        result, packets = self.query([TimeoutError(), self.HEADER + b"A5678", self.info(4)])
        self.assertEqual(result, 4)
        self.assertEqual(packets[-1][0], self.INFO + b"5678")

    def test_player_reply_including_zero_is_not_replaced(self):
        for count in (0, 3):
            with self.subTest(count=count):
                result, packets = self.query([self.HEADER + b"A1234", self.players(count)])
                self.assertEqual(result, count)
                self.assertEqual(len(packets), 2)

    def test_unavailable_queries_remain_unknown(self):
        result, packets = self.query([TimeoutError(), TimeoutError()])
        self.assertIsNone(result)
        self.assertEqual(len(packets), 2)

    def test_invalid_info_never_becomes_zero_players(self):
        for data in (b"", self.HEADER + b"I\x11missing-terminators", self.info(2)[:-2], self.HEADER + b"D\x03"):
            with self.subTest(data=data):
                result, _packets = self.query([TimeoutError(), data])
                self.assertIsNone(result)

    def test_truncated_player_entries_use_info_fallback(self):
        result, _packets = self.query([self.HEADER + b"D\x01" + b"x" * 12, self.info(1)])
        self.assertEqual(result, 1)

    def test_expired_observation_budget_does_not_open_a_socket(self):
        with patch.object(MODULE, "OBSERVATION_DEADLINE", 1), \
             patch.object(MODULE.time, "monotonic", return_value=2), \
             patch.object(MODULE.socket, "socket") as create:
            self.assertIsNone(MODULE.a2s_player_count(27016))
        create.assert_not_called()

    def test_queries_verified_bridge_address(self):
        result, packets = self.query([self.players(2)], "172.23.0.2")
        self.assertEqual(result, 2)
        self.assertEqual(packets[0][1], ("172.23.0.2", 27016))

    def test_public_or_invalid_query_addresses_never_create_a_socket(self):
        with patch.object(MODULE.socket, "socket") as create:
            for address in ("8.8.8.8", "0.0.0.0", "224.0.0.1", "169.254.1.1", "host", "::1", None):
                self.assertIsNone(MODULE.a2s_player_count(27016, address))
        create.assert_not_called()

    def test_invalid_ports_never_create_a_socket(self):
        with patch.object(MODULE.socket, "socket") as create:
            for port in (None, True, 0, -1, 65536, "27016"):
                self.assertIsNone(MODULE.a2s_player_count(port))
        create.assert_not_called()


class InstanceRuntimeStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-runtime-start-")
        self.root = Path(self.temp.name) / "famine-hub"
        instance = self.root / "instances" / INSTANCE
        (self.root / "instances").mkdir(parents=True)
        (self.root / "releases").mkdir()
        config = instance / "config" / "Cluster_1"
        (config / "Master").mkdir(parents=True)
        (config / "Caves").mkdir()
        (instance / "locks").mkdir()
        settings = {"roomName": "test", "maxPlayers": 6, "gameMode": "survival", "pvp": False}
        digest = "sha256:" + MODULE.hashlib.sha256(MODULE.canonical(settings)).hexdigest()
        files = {
            self.root / "host.json": {"schemaVersion": 1, "hostId": HOST},
            self.root / "registry.json": {"schemaVersion": 1, "hostId": HOST, "registryRevision": 4,
                "instances": [{"instanceId": INSTANCE, "lifecycle": "ready"}]},
            instance / "manifest.json": {"schemaVersion": 1, "hostId": HOST, "instanceId": INSTANCE,
                "configRevision": 4, "composeProject": "fh_" + INSTANCE.replace("-", ""),
                "cavesEnabled": True, "autostart": True, "ports": [
                    {"shard": shard, "purpose": purpose, "protocol": "udp", "bindAddress": "0.0.0.0",
                     "hostPort": port, "containerPort": port}
                    for shard, offset in (("master", 0), ("caves", 1))
                    for purpose, port in (("game", 11000 + offset), ("steamAuth", 8766 + offset), ("steamMaster", 27016 + offset))]},
            instance / "desired" / "world.json": {"schemaVersion": 1, "instanceId": INSTANCE,
                "revision": 4, "settings": settings},
            instance / "deployed-world.json": {"instanceId": INSTANCE, "revision": 4, "digest": digest, "settings": settings},
            instance / "active-release.json": {"dstReleaseId": RELEASE},
            self.root / "releases" / "catalog.json": {"hostId": HOST,
                "releases": [{"releaseId": RELEASE, "verified": True, "platform": "linux/amd64",
                    "runtimeVerified": False, "image": "hungerdesolate/dst-runtime", "imageDigest": "sha256:" + "a" * 64,
                    "dstBuild": "756039", "artifactDigest": "sha256:" + "b" * 64, "scriptVersion": "scripts-1",
                    "commands": {"master": [MODULE.SERVER_BINARY], "caves": [MODULE.SERVER_BINARY]}}]},
        }
        for path, value in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        for shard, ports in (("Master", (11000, 8766, 27016)), ("Caves", (11001, 8767, 27017))):
            (config / shard / "server.ini").write_text(
                f"[NETWORK]\nserver_port={ports[0]}\n[STEAM]\nauthentication_port={ports[1]}\nmaster_server_port={ports[2]}\n")
        (config / "cluster_token.txt").write_bytes(b"test-token")
        (config / "cluster.ini").write_text("[SHARD]\nshard_enabled=true\nmaster_port=10888\n")
        self.manifest = files[instance / "manifest.json"]
        self.release = dict(files[self.root / "releases" / "catalog.json"]["releases"][0], schemaVersion=1)
        (instance / "compose.yaml").write_text(HELPERS["render_compose"](self.manifest, self.release, str(instance)))
        for path in [self.root, self.root / "instances", self.root / "releases", instance,
                     instance / "locks",
                     instance / "config", config, config / "Master", config / "Caves"]:
            path.chmod(0o700)
        for path in files:
            path.chmod(0o600)
        for path in [config / "Master" / "server.ini", config / "Caves" / "server.ini", config / "cluster.ini", config / "cluster_token.txt", instance / "compose.yaml"]:
            path.chmod(0o600)
        self.instance = instance
        self.settings = settings
        original_lstat, original_fstat = Path.lstat, os.fstat

        def root_owned_path(path):
            values = list(original_lstat(path))
            values[4] = 1000 if self.instance / "config" in path.parents or path == self.instance / "config" else 0
            return os.stat_result(values)

        def root_owned_fd(fd):
            values = list(original_fstat(fd))
            values[4] = 0
            return os.stat_result(values)

        self.patches = [
            patch.object(MODULE, "ROOT", self.root),
            patch.object(MODULE.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", root_owned_path),
            patch.object(MODULE.os, "fstat", root_owned_fd),
            patch.object(MODULE.os, "fchown"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_starts_both_shards_and_waits_for_dedicated_process_udp_listeners(self):
        ids = {"master": "a" * 64, "caves": "b" * 64}

        def compose(_instance, _project, arguments, timeout=30):
            if arguments[:3] == ["ps", "--all", "--quiet"]:
                return ids[arguments[3]] + "\n"
            return ""

        def docker(arguments, timeout=30, capture=True):
            if arguments[0] == "inspect" and "Config.Labels" in arguments[-2]:
                shard = "master" if arguments[-1] == ids["master"] else "caves"
                return json.dumps({"com.hungerdesolate.hostId": HOST,
                    "com.hungerdesolate.instanceId": INSTANCE,
                    "com.hungerdesolate.role": shard})
            if arguments[0] == "inspect":
                if "State.Status" in arguments[-2]:
                    return json.dumps({"status": "running", "exitCode": 0, "restartCount": 0, "oomKilled": False})
                return "true"
            if arguments[0] == "exec":
                if "readlink" in arguments[-1]:
                    return "DST_RUNNING"
                ports = (10888, 11000, 27016, 38857) if arguments[1] == ids["master"] else (11001, 27017, 42722, 44559)
                return "sl local_address rem_address\n" + "\n".join(f"0: 00000000:{port:04X} 00000000:0000" for port in ports)
            raise AssertionError(arguments)

        with patch.object(MODULE, "compose", side_effect=compose) as compose_mock, \
             patch.object(MODULE, "docker", side_effect=docker):
            result = MODULE.start(REQUEST)
        self.assertEqual(result["runningShards"], ["caves", "master"])
        self.assertEqual(result["verificationLevel"], "localVerified")
        self.assertFalse(result["publicConnectivityVerified"])
        self.assertEqual(result["listeners"], {"master": [11000, 27016], "caves": [11001, 27017]})
        self.assertNotIn(8766, result["listeners"]["master"])
        self.assertNotIn(8767, result["listeners"]["caves"])
        self.assertIn("start", compose_mock.call_args.args[2])

    def test_stops_only_identity_verified_shards_and_reports_confirmed_stopped_state(self):
        request = dict(REQUEST, action="stop")
        with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
             patch.object(MODULE, "compose", return_value="") as compose, \
             patch.object(MODULE, "inspect_running", return_value=False):
            result = MODULE.start(request)
        self.assertEqual(compose.call_args.args[2], ["stop", "master", "caves"])
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["runningShards"], [])
        self.assertEqual(result["listeners"], {})

    def test_stop_does_not_claim_success_while_a_container_is_still_running(self):
        request = dict(REQUEST, action="stop")
        with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
             patch.object(MODULE, "compose", return_value=""), \
             patch.object(MODULE, "inspect_running", side_effect=[False, True]):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(request)
        self.assertEqual(failure.exception.code, "INSTANCE_STOP_RUNTIME_NOT_STOPPED")

    def test_restart_requires_running_shards_and_rechecks_normal_readiness(self):
        request = dict(REQUEST, action="restart")
        ports = lambda container: {10888, 11000, 27016} if container == "master" else {11001, 27017}
        with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
             patch.object(MODULE, "inspect_running", return_value=True), \
             patch.object(MODULE, "repair_runtime_layout"), \
             patch.object(MODULE, "compose", return_value="") as compose, \
             patch.object(MODULE, "runtime_running", return_value=True), \
             patch.object(MODULE, "process_ready", return_value=True), \
             patch.object(MODULE, "udp_ports", side_effect=ports), \
             patch.object(MODULE.time, "monotonic", side_effect=[0, 1]):
            result = MODULE.start(request)
        self.assertEqual(compose.call_args.args[2], ["restart", "master", "caves"])
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["runningShards"], ["caves", "master"])

    def test_restart_refuses_partial_or_stopped_instances_without_mutation(self):
        request = dict(REQUEST, action="restart")
        with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
             patch.object(MODULE, "inspect_running", side_effect=lambda container: container == "master"), \
             patch.object(MODULE, "repair_runtime_layout"), \
             patch.object(MODULE, "compose") as compose:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(request)
        self.assertEqual(failure.exception.code, "INSTANCE_RESTART_NOT_RUNNING")
        compose.assert_not_called()

    def test_process_probe_uses_container_executable_instead_of_host_docker_top(self):
        with patch.object(MODULE, "docker", return_value="DST_RUNNING") as docker:
            self.assertTrue(MODULE.process_ready("a" * 64))
        self.assertEqual(docker.call_args.args[0][:3], ["exec", "a" * 64, "/bin/sh"])
        self.assertIn("/proc/[0-9]*/exe", docker.call_args.args[0][-1])
        with patch.object(MODULE, "docker", return_value="DST_NOT_FOUND"):
            self.assertFalse(MODULE.process_ready("a" * 64))
        with patch.object(MODULE, "docker", return_value=None):
            self.assertIsNone(MODULE.process_ready("a" * 64))

    def test_port_probe_parses_both_headers_and_handles_unavailable_probe(self):
        output = "sl local_address rem_address\n0: 00000000:2AF8 00000000:0000\nsl local_address remote_address\n1: 00000000000000000000000000000000:6988 00000000000000000000000000000000:0000\n"
        with patch.object(MODULE, "docker", return_value=output):
            self.assertEqual(MODULE.udp_ports("a" * 64), {11000, 27016})
        with patch.object(MODULE, "docker", return_value=None):
            self.assertIsNone(MODULE.udp_ports("a" * 64))

    def test_missing_required_listener_reports_process_and_missing_ports_without_false_success(self):
        with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
             patch.object(MODULE, "inspect_running", return_value=True), \
             patch.object(MODULE, "runtime_running", return_value=True), \
             patch.object(MODULE, "repair_runtime_layout"), \
             patch.object(MODULE, "compose", return_value=""), \
             patch.object(MODULE, "process_ready", return_value=True), \
             patch.object(MODULE, "udp_ports", side_effect=lambda container: {10888, 27016} if container == "master" else {11001, 27017}), \
             patch.object(MODULE.time, "monotonic", side_effect=[0, 1, 121]), \
             patch.object(MODULE.time, "sleep"):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(REQUEST)
        self.assertEqual(failure.exception.code, "INSTANCE_START_RUNTIME_NOT_READY")
        self.assertTrue(failure.exception.details["masterProcessRunning"])
        self.assertEqual(failure.exception.details["masterMissingPorts"], [11000])
        self.assertEqual(failure.exception.details["masterObservedPorts"], [10888, 27016])
        self.assertEqual(failure.exception.details["cavesMissingPorts"], [])

    def test_shard_configuration_does_not_add_a_udp_listener(self):
        path = self.instance / "config" / "Cluster_1"
        (path / "cluster.ini").write_text("[SHARD]\nshard_enabled=true\nmaster_port=10889\n")
        self.assertEqual(MODULE.shard_ports(path, "master", True), {"game": 11000, "query": 27016})
        self.assertEqual(MODULE.shard_ports(path, "caves", True), {"game": 11001, "query": 27017})

    def test_missing_or_unknown_process_and_unknown_sockets_never_report_success(self):
        for running, available_ports in [(False, True), (None, True), (True, False)]:
            with self.subTest(process=running, portProbeAvailable=available_ports):
                ports = lambda container: ({10888, 11000, 27016} if container == "master" else {11001, 27017}) if available_ports else None
                with patch.object(MODULE, "verify_container", side_effect=lambda _instance, _project, _host, _id, shard: shard), \
                     patch.object(MODULE, "inspect_running", return_value=True), \
                     patch.object(MODULE, "runtime_running", return_value=True), \
                     patch.object(MODULE, "repair_runtime_layout"), \
                     patch.object(MODULE, "compose", return_value=""), \
                     patch.object(MODULE, "process_ready", return_value=running), \
                     patch.object(MODULE, "udp_ports", side_effect=ports), \
                     patch.object(MODULE.time, "monotonic", side_effect=[0, 1, 121]), \
                     patch.object(MODULE.time, "sleep"):
                    with self.assertRaises(MODULE.Rejected) as failure:
                        MODULE.start(REQUEST)
                self.assertEqual(failure.exception.code, "INSTANCE_START_RUNTIME_NOT_READY")
                self.assertIs(failure.exception.details["masterProcessRunning"], running)
                if available_ports:
                    self.assertEqual(failure.exception.details["masterMissingPorts"], [])
                else:
                    self.assertNotIn("masterMissingPorts", failure.exception.details)
                    self.assertNotIn("masterObservedPorts", failure.exception.details)

    def test_revision_conflict_stops_before_docker_mutation(self):
        stale = dict(REQUEST, expectedRevision=3)
        with patch.object(MODULE, "compose") as compose:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(stale)
        self.assertEqual(failure.exception.code, "REVISION_CONFLICT")
        compose.assert_not_called()

    def test_failure_envelope_preserves_safe_remote_code_and_stage(self):
        output = io.StringIO()
        with patch.object(MODULE, "start", side_effect=MODULE.Rejected("INSTANCE_START_COMPOSE_FAILED",
                {"failureStage": "startServices"})), redirect_stdout(output):
            MODULE.main(REQUEST)
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "INSTANCE_START_COMPOSE_FAILED")
        self.assertEqual(result["details"], {"failureStage": "startServices"})

    def test_restart_loop_is_reported_immediately_without_waiting(self):
        state = {"status": "restarting", "exitCode": 1, "restartCount": 7, "oomKilled": False}
        with patch.object(MODULE, "docker", return_value=json.dumps(state)), \
             patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.runtime_running("a" * 64, "master")
        self.assertEqual(failure.exception.code, "INSTANCE_START_CONTAINER_EXITED")
        self.assertEqual(failure.exception.details["exitCode"], 1)
        self.assertEqual(failure.exception.details["shard"], "master")
        sleep.assert_not_called()

    def test_legacy_layout_is_backed_up_and_recreated_with_correct_workdir(self):
        desired = HELPERS["render_compose"](self.manifest, self.release, str(self.instance))
        previous = desired.replace("working_dir: '/srv/dst/bin64'", "working_dir: '/srv'")
        (self.instance / "compose.yaml").write_text(previous)
        with patch.object(MODULE, "compose") as compose:
            MODULE.repair_runtime_layout(self.instance, self.manifest, self.release,
                self.manifest["composeProject"], ["master", "caves"])
        self.assertEqual((self.instance / "compose.yaml").read_text(), desired)
        self.assertEqual((self.instance / "compose.before-runtime-fix.yaml").read_text(), previous)
        self.assertEqual(compose.call_args_list[-1].args[2], ["create", "--force-recreate", "master", "caves"])

    def test_manual_compose_drift_is_not_overwritten(self):
        path = self.instance / "compose.yaml"
        path.write_text(path.read_text() + "# manual customization\n")
        previous = path.read_bytes()
        with patch.object(MODULE, "compose") as compose:
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.repair_runtime_layout(self.instance, self.manifest, self.release,
                    self.manifest["composeProject"], ["master", "caves"])
        self.assertEqual(failure.exception.code, "INSTANCE_START_COMPOSE_DRIFT")
        self.assertEqual(path.read_bytes(), previous)
        compose.assert_not_called()

    def test_previous_generated_compose_without_mod_release_is_migrated(self):
        current_manifest = dict(self.manifest, modsReleaseId="66666666-6666-4666-8666-666666666666")
        previous = HELPERS["render_compose"](self.manifest, self.release, str(self.instance))
        (self.instance / "compose.yaml").write_text(previous)
        with patch.object(MODULE, "compose") as compose:
            MODULE.repair_runtime_layout(self.instance, current_manifest, self.release,
                current_manifest["composeProject"], ["master", "caves"])
        desired = HELPERS["render_compose"](current_manifest, self.release, str(self.instance))
        self.assertEqual((self.instance / "compose.yaml").read_text(), desired)
        self.assertEqual(compose.call_args_list[-1].args[2], ["create", "--force-recreate", "master", "caves"])

    def containers(self, state="running"):
        return [{"id": ("a" if role == "master" else "b") * 64, "imageId": "sha256:" + "a" * 64,
                 "labels": {"com.hungerdesolate.hostId": HOST, "com.hungerdesolate.instanceId": INSTANCE,
                     "com.hungerdesolate.schemaVersion": "1", "com.hungerdesolate.role": role,
                     "com.docker.compose.project": self.manifest["composeProject"]},
                 "status": state, "bindings": {str(item["containerPort"]) + "/udp": [
                     {"HostIp": item["bindAddress"], "HostPort": str(item["hostPort"])}]
                     for item in self.manifest["ports"] if item["shard"] == role}}
                for role in ("master", "caves")]

    def inspection_request(self):
        return {"schemaVersion": 1, "requestId": REQUEST["requestId"], "hostId": HOST,
                "instanceIds": [INSTANCE], "expectedRegistryRevision": 4, "action": "inspect"}

    def inspection_docker(self, containers):
        def run(arguments, **_kwargs):
            if arguments[:2] == ["container", "ls"]:
                return "\n".join(item["id"] for item in containers)
            if arguments[0] == "inspect":
                return "\n".join(json.dumps(item) for item in containers)
            if arguments[0] == "exec":
                if arguments[2] == "cat":
                    return "756039\n"
                if "readlink" in arguments[-1]:
                    return "DST_RUNNING"
                ports = (10888, 11000, 27016) if arguments[1] == "a" * 64 else (11001, 27017)
                return "sl local_address rem_address\n" + "\n".join(
                    f"0: 00000000:{port:04X} 00000000:0000" for port in ports)
            raise AssertionError("Read-only inspection must not mutate Docker: " + arguments[0])
        return run

    def test_fresh_inspection_reads_real_version_and_ports_without_mutating_instance(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(self.containers())), \
             patch.object(MODULE, "a2s_player_count", side_effect=[2, 3]), \
             patch.object(MODULE.os, "fchown") as chown:
            response = MODULE.inspect_instances(self.inspection_request())
        snapshot = response["instances"][0]
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["installedBuild"], "756039")
        self.assertEqual(snapshot["buildSource"], "containerFile")
        self.assertEqual(snapshot["maxPlayers"], 6)
        self.assertEqual(snapshot["players"], 5)
        self.assertEqual(snapshot["desiredVsDeployed"], "inSync")
        self.assertEqual(len(snapshot["ports"]), 6)
        public = [port["hostPort"] for port in snapshot["ports"] if port["required"] and port["published"]]
        self.assertEqual(sorted(public), [11000, 11001, 27016, 27017])
        chown.assert_not_called()
        self.assertNotIn("test-token", json.dumps(response))
        self.assertEqual(before, {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_bridge_queries_work_when_localhost_forwarding_is_unavailable(self):
        containers = self.containers()
        project = self.manifest["composeProject"]
        addresses = {"172.23.0.2": 2, "172.23.0.3": 3}
        for index, container in enumerate(containers):
            container["networks"] = {project + "_instance": {"IPAddress": list(addresses)[index]}}
        destinations = []

        def socket_for_shard(*_args):
            sock = MagicMock()
            def send(_packet, destination):
                destinations.append(destination)
            def receive(_limit):
                address, port = destinations[-1]
                if address == "127.0.0.1":
                    raise TimeoutError()
                count = addresses[address]
                return (PlayerCountQueryTests.HEADER + b"D" + bytes([count])
                        + (b"\x00name\x00" + b"\x00" * 8) * count, (address, port))
            sock.sendto.side_effect = send
            sock.recvfrom.side_effect = receive
            return sock

        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(containers)), \
             patch.object(MODULE.socket, "socket", side_effect=socket_for_shard):
            snapshot = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        self.assertEqual(snapshot["players"], 5)
        self.assertEqual(destinations, [("172.23.0.2", 27016), ("172.23.0.3", 27017)])
        self.assertNotIn("PLAYERS_QUERY_UNAVAILABLE", snapshot["warnings"])

    def test_failed_bridge_query_falls_back_to_published_port(self):
        containers = self.containers()
        for container in containers:
            container["networks"] = {self.manifest["composeProject"] + "_instance": {"IPAddress": "172.23.0.2"}}
        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(containers)), \
             patch.object(MODULE, "a2s_player_count", side_effect=[None, 2, None, 3]) as query:
            snapshot = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        self.assertEqual(snapshot["players"], 5)
        self.assertEqual([call.args for call in query.call_args_list],
                         [(27016, "172.23.0.2"), (27016,), (27017, "172.23.0.2"), (27017,)])

    def test_bridge_address_must_belong_to_the_expected_compose_network(self):
        project = self.manifest["composeProject"]
        self.assertIsNone(MODULE.container_query_address({"networks": {"foreign_instance": {"IPAddress": "172.23.0.2"}}}, project))
        for address in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "::1", "bad", None):
            with self.subTest(address=address):
                container = {"networks": {project + "_instance": {"IPAddress": address}}}
                self.assertIsNone(MODULE.container_query_address(container, project))

    def test_player_total_requires_every_enabled_shard(self):
        for counts, total in (([2, None], None), ([None, 3], None), ([None, None], None), ([4, 4], None), ([0, 0], 0)):
            with self.subTest(counts=counts):
                with patch.object(MODULE, "docker", side_effect=self.inspection_docker(self.containers())), \
                     patch.object(MODULE, "a2s_player_count", side_effect=counts) as query:
                    snapshot = MODULE.inspect_instances(self.inspection_request())["instances"][0]
                self.assertEqual(snapshot["players"], total)
                self.assertEqual([call.args[0] for call in query.call_args_list], [27016, 27017])
                self.assertEqual(snapshot["status"], "running")
                self.assertEqual("PLAYERS_QUERY_UNAVAILABLE" in snapshot["warnings"], total is None)

    def test_reentering_queries_current_container_state_instead_of_start_result_cache(self):
        (self.instance / "runtime.json").write_text(json.dumps({"status": "running", "installedBuild": "stale"}))
        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(self.containers())):
            running = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(self.containers("created"))):
            stopped = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        self.assertEqual(running["status"], "running")
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["installedBuild"], "756039")
        self.assertEqual(stopped["buildSource"], "releaseMetadata")
        self.assertTrue(all(not port["listening"] for port in stopped["ports"]))

    def test_inspection_does_not_promote_foreign_container_or_unknown_docker_state(self):
        containers = self.containers()
        containers[0]["labels"]["com.hungerdesolate.hostId"] = "66666666-6666-4666-8666-666666666666"
        with patch.object(MODULE, "docker", side_effect=self.inspection_docker(containers)):
            snapshot = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        self.assertNotEqual(snapshot["status"], "running")
        self.assertIn("RESOURCE_IDENTITY_INVALID", snapshot["warnings"])
        with patch.object(MODULE, "docker", return_value=None):
            unavailable = MODULE.inspect_instances(self.inspection_request())["instances"][0]
        self.assertEqual(unavailable["status"], "unknown")
        self.assertIn("DOCKER_UNAVAILABLE", unavailable["warnings"])
        self.assertTrue(all(port["published"] is None or port["purpose"] == "shard" for port in unavailable["ports"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
