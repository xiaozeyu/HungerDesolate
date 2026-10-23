"""Verify restore task state is created with permissions accepted by its reader."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "restore_task", PROJECT / "remote-scripts/actions/restore-task.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TASK_ID = "33333333-3333-4333-8333-333333333333"
HOST_ID = "11111111-1111-4111-8111-111111111111"
INSTANCE_ID = "22222222-2222-4222-8222-222222222222"


class RestoreTaskStateTests(unittest.TestCase):
    def test_world_config_snapshot_is_validated_and_rebased_together(self):
        settings = {"gameMode": "survival"}
        digest = "sha256:" + __import__("hashlib").sha256(MODULE.canonical(settings)).hexdigest()
        desired = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                   "settings": settings, "secrets": {"tokenRef": "reference"}}
        deployed = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                    "settings": settings, "digest": digest}
        restored_desired, restored_deployed = MODULE.rebase_world_config(
            desired, deployed, INSTANCE_ID, 8, 10)
        self.assertEqual(restored_desired["revision"], 10)
        self.assertEqual(restored_deployed["revision"], 10)
        deployed["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.rebase_world_config(desired, deployed, INSTANCE_ID, 10, 11)
        self.assertEqual(failure.exception.code, "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID")

    def test_restore_recreates_secret_token_and_sets_runtime_file_ownership(self):
        with tempfile.TemporaryDirectory(prefix="restore-token-") as temporary:
            root = Path(temporary)
            instance = root / "instance"
            staging = root / "staging"
            secret_dir = instance / "desired-secrets"
            secret_dir.mkdir(parents=True, mode=0o700)
            config = staging / "config" / "Cluster_1"
            config.mkdir(parents=True, mode=0o700)
            secret_ref = "44444444-4444-4444-8444-444444444444"
            (secret_dir / (secret_ref + ".token")).write_bytes(b"private-token\n")
            (secret_dir / (secret_ref + ".token")).chmod(0o600)
            world = staging / "data" / "Master" / "save.zip"
            world.parent.mkdir(parents=True)
            world.write_bytes(b"world")
            desired = {"secrets": {"tokenRef": secret_ref}}
            MODULE.restore_cluster_token(instance, staging, desired)
            with patch.object(os, "chown") as chown:
                MODULE.assign_runtime_ownership(staging / "data" / "Master")
                MODULE.assign_runtime_ownership(staging / "config" / "Cluster_1")
            token = config / "cluster_token.txt"
            self.assertEqual(token.read_bytes(), b"private-token\n")
            self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
            self.assertTrue(any(call.args[1:] == (1000, 1000) for call in chown.call_args_list))

    def test_runtime_acceptance_requires_game_processes_and_configured_udp_listeners(self):
        with tempfile.TemporaryDirectory(prefix="restore-runtime-verify-") as temporary:
            instance = Path(temporary)
            config = instance / "config" / "Cluster_1"
            for role, game_port, query_port in (("Master", 11000, 27000), ("Caves", 11001, 27001)):
                shard = config / role
                shard.mkdir(parents=True, exist_ok=True)
                (shard / "server.ini").write_text(
                    f"[NETWORK]\nserver_port={game_port}\n[STEAM]\nmaster_server_port={query_port}\n"
                )
            (config / "cluster.ini").write_text("[SHARD]\nmaster_port=11002\n")
            containers = {"master": "a" * 64, "caves": "b" * 64}
            labels = [
                {"com.hungerdesolate.hostId": HOST_ID, "com.hungerdesolate.instanceId": INSTANCE_ID,
                 "com.hungerdesolate.role": role}
                for role in ("master", "caves")
            ]

            def run(args, **_kwargs):
                if args[1:4] == ["container", "ls", "--all"]:
                    return SimpleNamespace(returncode=0, stdout="\n".join(containers.values()))
                if args[1:3] == ["container", "inspect"]:
                    rows = [json.dumps(value) + "\trunning" for value in labels]
                    return SimpleNamespace(returncode=0, stdout="\n".join(rows))
                if args[1] == "exec" and "DST_RUNNING" in args[-1]:
                    return SimpleNamespace(returncode=0, stdout="DST_RUNNING")
                if args[1] == "exec":
                    role = "master" if args[2] == containers["master"] else "caves"
                    ports = [11000, 27000, 11002] if role == "master" else [11001, 27001]
                    if getattr(run, "omit_listener", False) and role == "caves":
                        ports.remove(27001)
                    rows = ["sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode"]
                    rows.extend(f"{index}: 00000000:{port:04X} 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 0"
                                for index, port in enumerate(ports))
                    return SimpleNamespace(returncode=0, stdout="\n".join(rows))
                raise AssertionError(args)

            run.omit_listener = False
            manifest = {"hostId": HOST_ID, "cavesEnabled": True}
            with patch.object(MODULE.subprocess, "run", side_effect=run):
                self.assertTrue(MODULE.verify_runtime_processes(instance, INSTANCE_ID, manifest, ["master", "caves"]))
                run.omit_listener = True
                self.assertFalse(MODULE.verify_runtime_processes(instance, INSTANCE_ID, manifest, ["master", "caves"]))

    def test_task_status_and_journal_are_private_and_readable(self):
        with tempfile.TemporaryDirectory(prefix="restore-task-") as temporary:
            root = Path(temporary)
            tasks = root / "restore-tasks"
            task_dir = tasks / TASK_ID
            task_dir.mkdir(parents=True, mode=0o700)
            tasks.chmod(0o700)
            state = {
                "schemaVersion": 1,
                "taskId": TASK_ID,
                "operationId": TASK_ID,
                "target": {"hostId": HOST_ID, "instanceId": INSTANCE_ID},
                "operation": "backupRestore",
                "status": "queued",
                "stage": "queued",
                "sequence": 1,
            }
            journal = MODULE.canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": "2026-10-22T00:00:00Z"}) + b"\n"
            with patch.object(MODULE, "TASKS", tasks):
                MODULE.atomic_json(task_dir / "status.json", state)
                MODULE.atomic_json(task_dir / "request.json", {"requestId": TASK_ID})
                MODULE.atomic_bytes(task_dir / "journal.jsonl", journal)
                self.assertEqual(stat.S_IMODE((task_dir / "status.json").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((task_dir / "request.json").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((task_dir / "journal.jsonl").stat().st_mode), 0o600)
                self.assertEqual(MODULE.load(TASK_ID)["status"], "queued")
                updated = MODULE.set_status(task_dir, "running", "preflight")
                self.assertEqual(updated["status"], "running")
                self.assertEqual(MODULE.load(TASK_ID)["sequence"], 2)


if __name__ == "__main__":
    unittest.main()
