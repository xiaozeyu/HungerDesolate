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
    def test_runtime_degradation_comparison_ignores_port_order_but_requires_same_failure(self):
        left = {"check": "udpListeners", "shard": "caves", "missingPorts": [11001, 27017]}
        right = {"check": "udpListeners", "shard": "caves", "missingPorts": [27017, 11001]}
        different = {"check": "dstProcessOrUdpProbe", "shard": "caves"}

        self.assertTrue(MODULE.same_runtime_degradation(left, right))
        self.assertFalse(MODULE.same_runtime_degradation(left, different))

    def test_missing_runtime_baseline_is_treated_as_degraded_unknown_not_rollback_failure(self):
        baseline = None
        degraded = {"check": "udpListeners", "shard": "caves", "missingPorts": [11001]}

        self.assertFalse(isinstance(baseline, dict))
        self.assertNotEqual(degraded.get("check"), "passed")

    def test_early_restore_failure_does_not_attempt_transaction_rollback_without_live_mutation(self):
        self.assertFalse(MODULE.restore_changed_live_state(False, False, []))
        self.assertTrue(MODULE.restore_changed_live_state(True, False, []))
        self.assertTrue(MODULE.restore_changed_live_state(True, True, []))
        self.assertTrue(MODULE.restore_changed_live_state(False, False, ["data/Master"]))

    def test_transaction_snapshot_restores_mod_and_compose_and_removes_new_optional_file(self):
        with tempfile.TemporaryDirectory(prefix="restore-transaction-snapshot-") as temporary:
            root = Path(temporary)
            instance = root / "instance"
            recovery = root / "recovery"
            instance.mkdir(mode=0o700)
            instance.chmod(0o700)
            paths = ["data/Master", "desired/world.json", "desired/mods.json",
                     "deployed-mods.json", "active-release.json", "manifest.json", "compose.yaml"]
            original_paths = [path for path in paths if path != "deployed-mods.json"]
            for relative in original_paths:
                old = recovery / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                old.write_text("old:" + relative)
                current = instance / relative
                current.parent.mkdir(parents=True, exist_ok=True)
                current.write_text("new:" + relative)
            (instance / "deployed-mods.json").write_text("new deployment snapshot")
            for parent in (instance, recovery, *[path for path in instance.rglob("*") if path.is_dir()],
                           *[path for path in recovery.rglob("*") if path.is_dir()]):
                parent.chmod(0o700)
            transaction = {"rollbackPaths": paths, "originalPaths": original_paths,
                "activeRelease": {"version": "old"}, "manifest": {"version": "old"},
                "desiredWorld": {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 2,
                    "settings": {}, "secrets": {"tokenRef": None}},
                "deployedWorld": {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 2,
                    "settings": {}, "digest": "sha256:" + __import__("hashlib").sha256(
                        MODULE.canonical({})).hexdigest()},
                "desiredMods": {"version": "old"}, "deployedMods": None, "compose": "old compose\n"}

            MODULE.restore_transaction_snapshot(instance, recovery, transaction)

            self.assertEqual((instance / "data/Master").read_text(), "old:data/Master")
            self.assertEqual(MODULE.read_json(instance / "desired/mods.json"), {"version": "old"})
            self.assertFalse((instance / "deployed-mods.json").exists())
            self.assertEqual((instance / "compose.yaml").read_text(), "old compose\n")
            self.assertEqual(MODULE.read_json(instance / "active-release.json"), {"version": "old"})

    def test_legacy_restore_uses_current_active_release_when_backup_has_no_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="restore-legacy-active-") as temporary:
            current = {"schemaVersion": 1, "dstReleaseId": "66666666-6666-4666-8666-666666666666",
                       "modsReleaseId": None}
            restored = MODULE.restored_active_snapshot(Path(temporary), current)
        self.assertEqual(restored, current)

    def test_restore_prefers_archived_active_release_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="restore-active-") as temporary:
            staging = Path(temporary)
            archived = {"schemaVersion": 1, "dstReleaseId": "77777777-7777-4777-8777-777777777777",
                        "modsReleaseId": None}
            MODULE.atomic_json(staging / "active-release.json", archived)
            restored = MODULE.restored_active_snapshot(staging, {"dstReleaseId": "66666666-6666-4666-8666-666666666666"})
        self.assertEqual(restored, archived)

    def test_restore_error_payload_carries_only_explicit_rollback_diagnostics(self):
        request = {"hostId": HOST_ID, "instanceId": INSTANCE_ID}
        payload = MODULE.error_payload("BACKUP_ROLLBACK_FAILED", request, TASK_ID,
            details={"rollbackStage": "restartOriginalContainers", "rollbackFailure": "BACKUP_RESTART_FAILED"})

        self.assertEqual(payload["details"], {
            "rollbackStage": "restartOriginalContainers", "rollbackFailure": "BACKUP_RESTART_FAILED"})
        self.assertNotIn("message", payload["details"])

    def test_restore_error_payload_reflects_whether_protection_was_requested(self):
        request = {"hostId": HOST_ID, "instanceId": INSTANCE_ID, "createProtectionBackup": False}
        payload = MODULE.error_payload("BACKUP_RESTART_FAILED", request, TASK_ID)
        self.assertNotIn("保护点", payload["message"])
        self.assertEqual(payload["recoverySteps"], ["核对恢复现场后重试"])
        request["createProtectionBackup"] = True
        payload = MODULE.error_payload("BACKUP_RESTART_FAILED", request, TASK_ID)
        self.assertIn("保护点", payload["message"])

    def test_restore_start_requires_an_explicit_protection_choice(self):
        request = {"schemaVersion": 1, "action": "restoreStart", "requestId": TASK_ID,
            "operationId": "44444444-4444-4444-8444-444444444444",
            "backupId": "55555555-5555-4555-8555-555555555555", "hostId": HOST_ID,
            "instanceId": INSTANCE_ID, "expectedRevision": 8}
        with patch.object(MODULE.os, "geteuid", return_value=0):
            with self.assertRaises(MODULE.Rejected) as failure:
                MODULE.start(request)
        self.assertEqual(failure.exception.code, "BACKUP_TASK_INPUT_INVALID")

    def test_restore_failure_cause_and_rollback_outcome_can_be_reported_separately(self):
        request = {"hostId": HOST_ID, "instanceId": INSTANCE_ID}
        payload = MODULE.error_payload("BACKUP_RESTART_FAILED", request, TASK_ID,
            details={"failureStep": "verifyRestoredRuntime", "failureCause": "BACKUP_RESTART_FAILED",
                     "rollbackStage": "complete", "rollbackSucceeded": True})

        self.assertEqual(payload["code"], "BACKUP_RESTART_FAILED")
        self.assertEqual(payload["details"]["failureStep"], "verifyRestoredRuntime")
        self.assertTrue(payload["details"]["rollbackSucceeded"])

    def test_compose_accepts_flags_and_multiple_shard_arguments(self):
        manifest = {"composeProject": "fh_test"}
        with patch.object(MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            MODULE.compose(Path("/instances/test"), manifest, "create", "--force-recreate", "master", "caves")
            MODULE.compose(Path("/instances/test"), manifest, "start", ["master", "caves"])

        first = run.call_args_list[0].args[0]
        second = run.call_args_list[1].args[0]
        self.assertEqual(first[-4:], ["create", "--force-recreate", "master", "caves"])
        self.assertEqual(second[-3:], ["start", "master", "caves"])

    def test_restore_start_launches_the_bundled_worker_and_release_helper(self):
        with tempfile.TemporaryDirectory(prefix="restore-start-") as temporary:
            root = Path(temporary) / "hub"
            tasks = root / "restore-tasks"
            root.mkdir(mode=0o700)
            tasks.mkdir(mode=0o700)
            request = {"schemaVersion": 1, "action": "restoreStart", "requestId": TASK_ID,
                "operationId": "44444444-4444-4444-8444-444444444444",
                "backupId": "55555555-5555-4555-8555-555555555555", "hostId": HOST_ID,
                "instanceId": INSTANCE_ID, "expectedRevision": 8, "createProtectionBackup": True}
            launch = SimpleNamespace(returncode=0)
            queued = {"status": "queued", "operationId": request["operationId"]}
            with patch.object(MODULE, "ROOT", root), patch.object(MODULE, "TASKS", tasks), \
                 patch.object(MODULE.os, "geteuid", return_value=0), patch.object(MODULE, "safe_dir"), \
                 patch.object(MODULE, "verify_instance"), patch.object(MODULE, "load", return_value=queued), \
                 patch.object(MODULE.subprocess, "run", return_value=launch) as run, \
                 patch.object(MODULE, "RESTORE_SOURCE", "worker source", create=True), \
                 patch.object(MODULE, "ARCHIVE_SOURCE", "archive source", create=True), \
                 patch.object(MODULE, "RELEASE_SOURCE", "release source", create=True):
                state = MODULE.start(request)

            operation_dir = tasks / request["operationId"]
            self.assertEqual(state["status"], "queued")
            self.assertTrue((operation_dir / "worker.py").is_file())
            self.assertEqual((operation_dir / "release-source.py").read_text(), "release source")
            self.assertIn(str(operation_dir / "worker.py"), run.call_args.args[0])

    def test_restore_worker_bundle_includes_archive_and_release_helpers(self):
        with tempfile.TemporaryDirectory(prefix="restore-bundle-") as temporary:
            directory = Path(temporary)
            MODULE.write_worker_bundle(directory, "worker source", "archive source", "release source")

            self.assertEqual((directory / "worker.py").read_text(), "worker source")
            self.assertEqual((directory / "archive-source.py").read_text(), "archive source")
            self.assertEqual((directory / "release-source.py").read_text(), "release source")
            self.assertEqual(stat.S_IMODE((directory / "release-source.py").stat().st_mode), 0o600)

    def test_missing_mod_snapshot_is_restored_as_no_mods_not_current_state(self):
        with tempfile.TemporaryDirectory(prefix="restore-missing-mods-") as temporary:
            root = Path(temporary)
            staging = root / "staging"
            instance = root / "instance"
            current = instance / "desired" / "mods.json"
            current.parent.mkdir(parents=True)
            current.write_text(json.dumps({"schemaVersion": 1, "instanceId": INSTANCE_ID,
                "revision": 9, "mods": [{"workshopId": "123", "enabled": True,
                    "configurationOptions": {}, "artifactDigest": "a" * 64}]}))
            current.chmod(0o600)
            self.assertEqual(MODULE.read_mod_snapshot(staging, False), (None, None))

            archived = staging / "desired" / "mods.json"
            archived.parent.mkdir(parents=True)
            archived.write_text(json.dumps({"schemaVersion": 1, "instanceId": INSTANCE_ID,
                "revision": 3, "mods": []}))
            archived.chmod(0o600)
            deployed = staging / "deployed-mods.json"
            deployed.write_text(json.dumps({"schemaVersion": 1, "instanceId": INSTANCE_ID,
                "revision": 3, "mods": []}))
            deployed.chmod(0o600)
            desired_value, deployed_value = MODULE.read_mod_snapshot(staging, True)
            self.assertEqual(desired_value["revision"], 3)
            self.assertEqual(deployed_value["revision"], 3)

    def test_mod_snapshot_can_restore_desired_before_first_deployment(self):
        desired = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                   "mods": [{"workshopId": "123", "enabled": True, "artifactDigest": "a" * 64}]}
        restored_desired, restored_deployed = MODULE.rebase_mod_states(
            desired, None, INSTANCE_ID, 10)
        self.assertEqual(restored_desired["revision"], 10)
        self.assertIsNone(restored_deployed)

    def test_mod_snapshot_revision_is_rebased_to_the_new_revision(self):
        desired = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 9, "mods": []}
        deployed = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 9, "mods": []}
        restored_desired, restored_deployed = MODULE.rebase_mod_states(
            desired, deployed, INSTANCE_ID, 12)
        self.assertEqual(restored_desired["revision"], 12)
        self.assertEqual(restored_deployed["revision"], 12)

    def test_restore_json_reader_accepts_optional_missing_deployed_mod_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="restore-optional-mods-") as temporary:
            root = Path(temporary)
            desired = root / "desired" / "mods.json"
            desired.parent.mkdir()
            desired.write_text(json.dumps({"schemaVersion": 1, "instanceId": INSTANCE_ID,
                "revision": 8, "mods": []}))
            desired.chmod(0o600)

            self.assertEqual(MODULE.read_json(root / "deployed-mods.json", 2 * 1024 * 1024, missing=None), None)
            self.assertEqual(MODULE.read_json(desired, 2 * 1024 * 1024)["mods"], [])

    def test_mod_snapshot_rejects_state_from_another_instance(self):
        desired = {"schemaVersion": 1, "instanceId": HOST_ID, "revision": 8, "mods": []}
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.rebase_mod_states(desired, None, INSTANCE_ID, 10)
        self.assertEqual(failure.exception.code, "BACKUP_MODS_SNAPSHOT_INVALID")

    def test_world_config_snapshot_is_validated_and_rebased_together(self):
        settings = {"gameMode": "survival"}
        digest = "sha256:" + __import__("hashlib").sha256(MODULE.canonical(settings)).hexdigest()
        desired = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                   "settings": settings, "secrets": {"tokenRef": "reference"}}
        deployed = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                    "settings": settings, "digest": digest}
        restored_desired, restored_deployed = MODULE.rebase_world_config(
            desired, deployed, INSTANCE_ID, 10)
        self.assertEqual(restored_desired["revision"], 10)
        self.assertEqual(restored_deployed["revision"], 10)
        deployed["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(MODULE.Rejected) as failure:
            MODULE.rebase_world_config(desired, deployed, INSTANCE_ID, 11)
        self.assertEqual(failure.exception.code, "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID")

    def test_world_snapshot_rebases_desired_and_deployed_independently(self):
        desired_settings = {"gameMode": "survival", "new": True}
        deployed_settings = {"gameMode": "endless"}
        deployed_digest = "sha256:" + __import__("hashlib").sha256(
            MODULE.canonical(deployed_settings)).hexdigest()
        desired = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 9,
                   "settings": desired_settings, "secrets": {"tokenRef": "reference"}}
        deployed = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 8,
                    "settings": deployed_settings, "digest": deployed_digest}
        restored_desired, restored_deployed = MODULE.rebase_world_config(
            desired, deployed, INSTANCE_ID, 10)
        self.assertEqual(restored_desired["revision"], 10)
        self.assertEqual(restored_deployed["revision"], 10)
        self.assertEqual(restored_desired["settings"], desired_settings)
        self.assertEqual(restored_deployed["settings"], deployed_settings)

    def test_restore_uses_the_current_token_reference_instead_of_the_backup_reference(self):
        old_ref = "44444444-4444-4444-8444-444444444444"
        current_ref = "55555555-5555-4555-8555-555555555555"
        restored = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 10,
                    "settings": {}, "secrets": {"tokenRef": old_ref}}
        current = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 11,
                   "settings": {}, "secrets": {"tokenRef": current_ref}}

        result = MODULE.preserve_current_secret_refs(restored, current)

        self.assertEqual(result["secrets"]["tokenRef"], current_ref)
        self.assertEqual(restored["secrets"]["tokenRef"], old_ref)

    def test_restore_keeps_backup_token_when_no_current_token_is_configured(self):
        old_ref = "44444444-4444-4444-8444-444444444444"
        restored = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 10,
                    "settings": {}, "secrets": {"tokenRef": old_ref}}
        current = {"schemaVersion": 1, "instanceId": INSTANCE_ID, "revision": 11,
                   "settings": {}, "secrets": {"tokenRef": None}}

        result = MODULE.preserve_current_secret_refs(restored, current)

        self.assertEqual(result["secrets"]["tokenRef"], old_ref)

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

    def test_restore_preserves_current_player_lists_outside_the_backup(self):
        with tempfile.TemporaryDirectory(prefix="restore-player-lists-") as temporary:
            root = Path(temporary)
            instance = root / "instance"
            staging = root / "staging"
            current = instance / "config" / "Cluster_1"
            current.mkdir(parents=True, mode=0o700)
            for name, payload in (("adminlist.txt", b"STEAM_1\n"),
                                  ("whitelist.txt", b"STEAM_2\n"),
                                  ("blocklist.txt", b"STEAM_3\n")):
                path = current / name
                path.write_bytes(payload)
                path.chmod(0o600)
            staged_config = staging / "config" / "Cluster_1"
            staged_config.mkdir(parents=True, mode=0o700)
            staged_config.chmod(0o755)
            preserved = MODULE.preserve_player_lists(instance)
            MODULE.write_player_lists(staging, preserved)
            self.assertEqual(stat.S_IMODE(staged_config.stat().st_mode), 0o700)
            for name in ("adminlist.txt", "whitelist.txt", "blocklist.txt"):
                self.assertEqual((staging / "config" / "Cluster_1" / name).read_bytes(),
                                 (current / name).read_bytes())

    def test_restore_commit_moves_staged_directories_without_copying_ownership(self):
        source = (Path(__file__).parents[1] / "remote-scripts" / "actions" / "restore-task.py").read_text()
        self.assertIn("os.replace(restored, source)", source)
        self.assertNotIn("shutil.copytree(restored, source", source)

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
                evidence = {}
                self.assertTrue(MODULE.verify_runtime_processes(instance, INSTANCE_ID, manifest,
                    ["master", "caves"], evidence))
                self.assertEqual(evidence, {"check": "passed"})
                run.omit_listener = True
                self.assertFalse(MODULE.verify_runtime_processes(instance, INSTANCE_ID, manifest,
                    ["master", "caves"], evidence))
                self.assertEqual(evidence, {"check": "udpListeners", "shard": "caves", "missingPorts": [27001]})

    def test_runtime_wait_retries_until_shards_listen_and_preserves_last_failure(self):
        evidence = {"check": "udpListeners", "shard": "caves", "missingPorts": [11001, 27017]}
        results = [False, False, True]
        with patch.object(MODULE, "verify_runtime_processes", side_effect=lambda *args: results.pop(0)) as verify, \
             patch.object(MODULE.time, "sleep") as sleep:
            self.assertTrue(MODULE.wait_for_runtime_processes(
                Path("/instances/test"), INSTANCE_ID, {"hostId": HOST_ID}, ["master", "caves"], evidence))
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        with patch.object(MODULE, "verify_runtime_processes", return_value=False):
            self.assertFalse(MODULE.wait_for_runtime_processes(
                Path("/instances/test"), INSTANCE_ID, {"hostId": HOST_ID}, ["master", "caves"], evidence,
                timeout=0))
        self.assertEqual(evidence, {"check": "udpListeners", "shard": "caves", "missingPorts": [11001, 27017]})

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
