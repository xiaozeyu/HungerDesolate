"""Test persistent instance-create task admission and explicit deployment gating."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]


def load_bundle():
    module = types.ModuleType("instance_create_bundle")
    namespace = vars(module)
    for path in [
        PROJECT / "remote-scripts/actions/instance-create-control.py",
        PROJECT / "remote-scripts/actions/instance-create-task.py",
    ]:
        exec(path.read_text(), namespace)
    return module


CONTROL = load_bundle()
HOST = "11111111-1111-4111-8111-111111111111"
PLAN = "22222222-2222-4222-8222-222222222222"
OPERATION = "33333333-3333-4333-8333-333333333333"


def valid_release():
    return {"schemaVersion": 1, "releaseId": "44444444-4444-4444-8444-444444444444",
            "platform": "linux/amd64", "image": "registry.example/dst",
            "imageDigest": "sha256:" + "a" * 64, "dstBuild": "build-2026",
            "artifactDigest": "sha256:" + "b" * 64, "scriptVersion": "scripts-1",
            "verified": True, "runtimeVerified": False, "commands": {"master": ["/opt/dst", "master"],
                                             "caves": ["/opt/dst", "caves"]}}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-create-task-")
        root = Path(self.temp.name) / "famine-hub"
        root.mkdir(mode=0o700)
        (root / "locks").mkdir(mode=0o700)
        (root / "releases").mkdir(mode=0o700)
        (root / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST}))
        (root / "registry.json").write_text(json.dumps({"schemaVersion": 1, "hostId": HOST,
                                                         "registryRevision": 3, "instances": [],
                                                         "portReservations": [], "operations": []}))
        (root / "host.json").chmod(0o600)
        (root / "registry.json").chmod(0o600)
        release = valid_release()
        catalog = {"schemaVersion": 1, "hostId": HOST,
                   "releases": [{key: value for key, value in release.items() if key != "schemaVersion"}]}
        (root / "releases" / "catalog.json").write_text(json.dumps(catalog))
        (root / "releases" / "catalog.json").chmod(0o600)
        self.root = root
        catalog_digest = CONTROL.digest(catalog)
        self.request = {"action": "start", "schemaVersion": 1, "operationId": OPERATION,
                        "planId": PLAN, "hostId": HOST, "expectedRegistryRevision": 3,
                        "input": {"displayName": "生存一服", "autostart": True,
                                   "startAfterCreate": True, "portPolicy": "auto", "cavesEnabled": False,
                                   "releaseId": release["releaseId"], "releaseCatalogDigest": catalog_digest,
                                   "expectedPorts": CONTROL.allocate(set(), False)}}
        original_lstat, original_fstat = Path.lstat, os.fstat
        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        self.patches = [
            patch.object(CONTROL, "ROOT", root),
            patch.object(CONTROL, "CATALOG", root / "releases" / "catalog.json"),
            patch.object(CONTROL, "CATALOG", root / "releases" / "catalog.json"),
            patch.object(CONTROL, "LOCKS", root / "locks"),
            patch.object(CONTROL, "LOCK_PATH", root / "locks" / "registry.lock"),
            patch.object(CONTROL, "OWNER_PATH", root / "locks" / "registry-owner.json"),
            patch.object(CONTROL, "TASKS", root / "tasks"),
            patch.object(CONTROL, "socket_ports", return_value=set()),
            patch.object(CONTROL, "docker_ports", return_value=set()),
            patch.object(CONTROL.os, "geteuid", return_value=0),
            patch.object(Path, "lstat", lambda path: root_owned(original_lstat(path))),
            patch.object(CONTROL.os, "fstat", lambda fd: root_owned(original_fstat(fd))),
        ]
        for item in self.patches:
            item.start()
        runtime_source = (PROJECT / "remote-scripts/actions/instance-create-task.py").read_text()
        CONTROL.RUNTIME_SOURCE = runtime_source + f"\nROOT = Path({str(root)!r})\nTASKS = ROOT / 'tasks'\n"
        CONTROL.RELEASE_SOURCE = (PROJECT / "remote-scripts/actions/instance-release.py").read_text()
        CONTROL.DOCKER_SOURCE = (PROJECT / "remote-scripts/actions/instance-docker.py").read_text()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_admission_persists_task_before_launch_and_worker_keeps_explicit_failure(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch) as runner:
            state = CONTROL.start(self.request, "# fixed worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(state["status"], "queued")
        self.assertEqual(state["sequence"], 1)
        task = self.root / "tasks" / OPERATION
        self.assertTrue((task / "request.json").exists())
        self.assertTrue((task / "status.json").exists())
        self.assertEqual(runner.call_args.args[0][0], "/usr/bin/systemd-run")
        self.assertTrue((task / "release.json").exists())
        (task / "release.json").unlink()

        worker_source = PROJECT / "remote-scripts/actions/instance-create-worker.py"
        (task / "worker.py").write_text(worker_source.read_text())
        with patch.object(CONTROL.os, "geteuid", return_value=0), \
             patch.object(CONTROL.subprocess, "run", return_value=launch):
            worker = __import__("runpy").run_path(str(task / "worker.py"))
            worker["main"](str(task))
        status = json.loads((task / "status.json").read_text())
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["stage"], "download")
        self.assertEqual(status["error"]["code"], "CREATE_RELEASE_REQUIRED")
        manifest = json.loads((self.root / "instances" / state["target"]["instanceId"] / "manifest.json").read_text())
        self.assertEqual(manifest["instanceId"], state["target"]["instanceId"])
        self.assertNotIn("token", json.dumps(manifest))
        self.assertTrue((self.root / "instances" / state["target"]["instanceId"] / "data" / "Master").is_dir())
        self.assertGreaterEqual(status["sequence"], 3)

    def test_same_operation_replays_and_changed_payload_is_rejected(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            first = CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
            second = CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(first, second)
        changed = json.loads(json.dumps(self.request))
        changed["input"]["displayName"] = "另一服"
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            with self.assertRaises(CONTROL.TaskRejected) as failure:
                CONTROL.start(changed, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(str(failure.exception), "OPERATION_ID_REUSED")

    def test_status_marks_dead_running_worker_interrupted(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        task = self.root / "tasks" / OPERATION
        CONTROL.update_status(task, "running", "preflight")
        with patch.object(CONTROL.subprocess, "run", return_value=type("Result", (), {"returncode": 3})()):
            status = CONTROL.status(OPERATION)
        self.assertEqual(status["status"], "interrupted")
        self.assertEqual(status["error"]["code"], "CREATE_RESULT_UNKNOWN")

    def test_controller_launch_failure_is_persisted_with_remote_changed(self):
        launch = type("Result", (), {"returncode": 1})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            state = CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"]["code"], "CREATE_CONTROLLER_START_FAILED")
        self.assertTrue(state["error"]["remoteChanged"])
        self.assertTrue((self.root / "tasks" / OPERATION / "journal.jsonl").exists())

    def test_unverified_release_is_rejected_during_admission(self):
        catalog = json.loads((self.root / "releases" / "catalog.json").read_text())
        catalog["releases"][0]["verified"] = False
        (self.root / "releases" / "catalog.json").write_text(json.dumps(catalog))
        request = json.loads(json.dumps(self.request))
        request["input"]["releaseCatalogDigest"] = CONTROL.digest(catalog)
        with self.assertRaises(CONTROL.TaskRejected) as failure:
            CONTROL.start(request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(str(failure.exception), "CREATE_RELEASE_REQUIRED")

    def test_invalid_release_stops_before_compose_publish(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        task = self.root / "tasks" / OPERATION
        (task / "release.json").write_text(json.dumps({"schemaVersion": 1, "verified": False}))
        (task / "release.json").chmod(0o600)
        worker_source = PROJECT / "remote-scripts/actions/instance-create-worker.py"
        (task / "worker.py").write_text(worker_source.read_text())
        with patch.object(CONTROL.os, "geteuid", return_value=0), \
             patch.object(CONTROL.subprocess, "run", return_value=launch):
            worker = __import__("runpy").run_path(str(task / "worker.py"))
            worker["main"](str(task))
        status = json.loads((task / "status.json").read_text())
        self.assertEqual(status["error"]["code"], "CREATE_RELEASE_INVALID")
        self.assertFalse(any((self.root / "instances").rglob("compose.yaml")))

    def test_compose_config_failure_keeps_staging_without_resource_plan(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        task = self.root / "tasks" / OPERATION
        (task / "release.json").write_text(json.dumps(valid_release()))
        (task / "release.json").chmod(0o600)
        worker_source = PROJECT / "remote-scripts/actions/instance-create-worker.py"
        (task / "worker.py").write_text(worker_source.read_text())
        def command_result(command, **_kwargs):
            return type("Result", (), {"returncode": 1 if command[:2] == ["docker", "compose"] else 0})()
        with patch.object(CONTROL.os, "geteuid", return_value=0), \
             patch.object(CONTROL.subprocess, "run", side_effect=command_result):
            worker = __import__("runpy").run_path(str(task / "worker.py"))
            worker["main"](str(task))
        status = json.loads((task / "status.json").read_text())
        staging = next((self.root / "instances").rglob("staging")) / OPERATION
        self.assertEqual(status["error"]["code"], "CREATE_COMPOSE_CONFIG_FAILED")
        self.assertTrue((staging / "compose.yaml").exists())
        self.assertFalse((staging / "resource-plan.json").exists())

    def test_base_verified_release_creates_containers_without_runtime_smoke_test(self):
        launch = type("Result", (), {"returncode": 0})()
        with patch.object(CONTROL.subprocess, "run", return_value=launch):
            state = CONTROL.start(self.request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        task = self.root / "tasks" / OPERATION
        release = valid_release()
        release["runtimeVerified"] = False
        (task / "release.json").write_text(json.dumps(release))
        (task / "release.json").chmod(0o600)
        docker_stub = "\n".join([
            "class DockerRejected(Exception): pass",
            "def ensure_network(project, host_id, instance_id): return project + '_instance'",
            "def create_containers(path, project, root): return {'project': project, 'created': True, 'started': False}",
            "def verify_containers(project, host_id, instance_id, manifest, root): return {'containerIds': ['a' * 64], 'roles': ['master'], 'mountsVerified': True, 'portsVerified': True}",
        ]) + "\n"
        (task / "instance-docker.py").write_text(docker_stub)
        worker_source = PROJECT / "remote-scripts/actions/instance-create-worker.py"
        (task / "worker.py").write_text(worker_source.read_text())
        with patch.object(CONTROL.os, "geteuid", return_value=0), \
             patch.object(CONTROL.subprocess, "run", return_value=launch):
            worker = __import__("runpy").run_path(str(task / "worker.py"))
            self.assertEqual(worker["main"](str(task)), 0)
        status = json.loads((task / "status.json").read_text())
        instance = self.root / "instances" / state["target"]["instanceId"]
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["stage"], "finalize")
        self.assertTrue((instance / "compose.yaml").exists())
        self.assertTrue((instance / "runtime.json").exists())
        manifest = json.loads((instance / "manifest.json").read_text())
        self.assertEqual(manifest["dstReleaseId"], release["releaseId"])

    def test_secret_fields_are_rejected_before_task_persistence(self):
        request = json.loads(json.dumps(self.request))
        request["input"]["token"] = "secret"
        with self.assertRaises(CONTROL.TaskRejected) as failure:
            CONTROL.start(request, "# worker", CONTROL.RUNTIME_SOURCE, CONTROL.RELEASE_SOURCE, CONTROL.DOCKER_SOURCE)
        self.assertEqual(str(failure.exception), "CREATE_INPUT_INVALID")
        self.assertFalse((self.root / "tasks" / OPERATION).exists())

    def test_controller_bundle_imports_base64_before_dispatch(self):
        sources = [
            (PROJECT / "remote-scripts/actions/instance-create-control.py").read_text(),
            (PROJECT / "remote-scripts/actions/instance-create-task.py").read_text(),
        ]
        source = "\nimport base64\n" + "\n".join(sources) + "\nmain(json.loads(base64.b64decode('e30=').decode('utf-8')))\n"
        namespace = {}
        exec(compile(source, "<instance-create-controller>", "exec"), namespace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
