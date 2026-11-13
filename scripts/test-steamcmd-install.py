"""Run the production installer against isolated SteamCMD/timeout fixtures, with no network."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT / "docker/runtime/install-dst.sh"

STEAMCMD = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> commands.log
if [ "$1" = +quit ]; then phase=bootstrap; else phase=download; fi
count_file="$phase.count"
count=0
if [ -f "$count_file" ]; then count=$(cat "$count_file"); fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
mkdir -p logs
echo 'mock SteamCMD diagnostic' > logs/bootstrap_log.txt
case "$DST_TEST_SCENARIO:$phase" in
  bootstrap_once:bootstrap) if [ "$count" -eq 1 ]; then exit 8; fi ;;
  bootstrap_fails:bootstrap) exit 8 ;;
  bootstrap_partial_timeout:bootstrap)
    if [ "$count" -eq 1 ]; then
      printf 'partial update' > saved-update.part
      exit 124
    fi
    test -s saved-update.part || exit 99 ;;
  bootstrap_crash:bootstrap)
    echo 'Assertion Failed: mock SteamCMD failure before minidump upload'
    echo 'Finished uploading minidump: success = yes'
    exit 254 ;;
  bootstrap_noisy_crash:bootstrap)
    echo 'Assertion Failed: mock failure hidden by progress'
    for ((line=0; line<200; line++)); do
      echo 'Downloading update / uploading minidump diagnostic'
    done
    exit 254 ;;
  download_once:download) if [ "$count" -eq 1 ]; then exit 8; fi ;;
  download_fails:download) exit 8 ;;
  fatal:download) exit 127 ;;
esac
if [ "$phase" = download ]; then
  mkdir -p dst/bin64 dst/data/databundles
  printf '#!/bin/sh\nexit 0\n' > dst/bin64/dontstarve_dedicated_server_nullrenderer_x64
  chmod 700 dst/bin64/dontstarve_dedicated_server_nullrenderer_x64
  printf '756039\n' > dst/version.txt
  if [ "$DST_TEST_SCENARIO" != missing_scripts ]; then printf 'mock scripts' > dst/data/databundles/scripts.zip; fi
  if [ "$DST_TEST_SCENARIO" = already_current ]; then
    echo "Success! App '343050' already up to date."
  elif [ "$DST_TEST_SCENARIO" != missing_success ]; then
    echo "Success! App '343050' fully installed."
  fi
fi
'''


class SteamcmdInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="steamcmd-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copyfile(INSTALLER, self.root / "install-dst.sh")
        (self.root / "steamcmd.sh").write_text(STEAMCMD)
        (self.root / "steamcmd.sh").chmod(0o700)
        bins = self.root / "tools"
        bins.mkdir()
        for name, source in {
            "timeout": '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> timeout.log\nshift 3\nexec "$@"\n',
            "sleep": '#!/bin/sh\nexit 0\n',
        }.items():
            path = bins / name
            path.write_text(source)
            path.chmod(0o700)
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in {"STEAMCMD_BOOTSTRAP_DEADLINE", "STEAMCMD_DOWNLOAD_DEADLINE"}}
        self.environment["PATH"] = str(bins) + os.pathsep + os.environ["PATH"]

    def run_phase(self, phase, scenario="success"):
        result = subprocess.run(["bash", "install-dst.sh", phase], cwd=self.root,
            env=dict(self.environment, DST_TEST_SCENARIO=scenario),
            capture_output=True, text=True, timeout=10, check=False)
        self.assertNotIn("unbound variable", result.stderr)
        return result

    def count(self, phase):
        path = self.root / (phase + ".count")
        return int(path.read_text()) if path.exists() else 0

    def test_bootstrap_is_separate_from_anonymous_download_and_requires_artifacts(self):
        self.assertEqual(self.run_phase("bootstrap").returncode, 0)
        self.assertEqual(self.count("download"), 0)
        result = self.run_phase("download")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = (self.root / "commands.log").read_text().splitlines()
        self.assertEqual(commands[0], "+quit")
        self.assertIn("+@ShutdownOnFailedCommand 1", commands[1])
        self.assertIn("+@NoPromptForPassword 1", commands[1])
        self.assertIn("+login anonymous +app_update 343050 validate +quit", commands[1])
        deadlines = (self.root / "timeout.log").read_text().splitlines()
        self.assertEqual(len(deadlines), 2)
        self.assertIn("3600s", deadlines[0])
        self.assertIn("1800s", deadlines[1])

    def test_invalid_or_unbounded_deadline_never_starts_steamcmd(self):
        for deadline in ["0s", "0", "infinity", "-1s", "bad"]:
            with self.subTest(deadline=deadline):
                result = subprocess.run(["bash", "install-dst.sh", "bootstrap"], cwd=self.root,
                    env=dict(self.environment, DST_TEST_SCENARIO="success", STEAMCMD_BOOTSTRAP_DEADLINE=deadline),
                    capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn("STEAMCMD_DEADLINE_INVALID", result.stderr)
                self.assertEqual(self.count("bootstrap"), 0)

    def test_timeout_retry_preserves_partial_update_files_within_the_build(self):
        result = self.run_phase("bootstrap", "bootstrap_partial_timeout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count("bootstrap"), 2)
        self.assertIn("exitCode=124", result.stderr)
        self.assertTrue((self.root / "saved-update.part").is_file())

    def test_slow_bootstrap_deadline_can_be_overridden(self):
        result = subprocess.run(["bash", "install-dst.sh", "bootstrap"], cwd=self.root,
            env=dict(self.environment, DST_TEST_SCENARIO="success", STEAMCMD_BOOTSTRAP_DEADLINE="2400s"),
            capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2400s", (self.root / "timeout.log").read_text())

    def test_bootstrap_update_restart_can_retry_without_accepting_failure(self):
        result = self.run_phase("bootstrap", "bootstrap_once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count("bootstrap"), 2)
        self.assertIn("exitCode=8", result.stderr)

    def test_persistent_bootstrap_failure_is_bounded_and_keeps_exit_code(self):
        result = self.run_phase("bootstrap", "bootstrap_fails")
        self.assertEqual(result.returncode, 8)
        self.assertEqual(self.count("bootstrap"), 3)
        self.assertEqual(self.count("download"), 0)
        self.assertIn("STEAMCMD_BOOTSTRAP_FAILED", result.stderr)
        self.assertIn("mock SteamCMD diagnostic", result.stdout)

    def test_download_failure_can_retry_but_never_reports_success_on_exhaustion(self):
        result = self.run_phase("download", "download_fails")
        self.assertEqual(result.returncode, 8)
        self.assertEqual(self.count("download"), 3)
        self.assertIn("STEAMCMD_DOWNLOAD_FAILED", result.stderr)
        self.assertNotIn("phase=download complete", result.stdout)

    def test_successful_download_retry_requires_valid_final_result(self):
        result = self.run_phase("download", "download_once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count("download"), 2)

    def test_zero_exit_without_success_marker_is_not_a_valid_release(self):
        result = self.run_phase("download", "missing_success")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DST_ARTIFACT_INVALID", result.stderr)

    def test_already_current_with_complete_artifacts_is_successful(self):
        result = self.run_phase("download", "already_current")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_zero_exit_with_missing_game_resources_is_not_a_valid_release(self):
        result = self.run_phase("download", "missing_scripts")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DST_ARTIFACT_INVALID", result.stderr)

    def test_non_retryable_failure_does_not_loop(self):
        result = self.run_phase("download", "fatal")
        self.assertEqual(result.returncode, 127)
        self.assertEqual(self.count("download"), 1)

    def test_bootstrap_crash_keeps_exit_code_and_prints_phase_diagnostics(self):
        result = self.run_phase("bootstrap", "bootstrap_crash")
        self.assertEqual(result.returncode, 254)
        self.assertEqual(self.count("bootstrap"), 1)
        self.assertEqual(self.count("download"), 0)
        self.assertIn("STEAMCMD_BOOTSTRAP_FAILED", result.stderr)
        self.assertIn("exitCode=254", result.stderr)
        self.assertIn("deadline=3600s", result.stdout)
        self.assertIn("==> SteamCMD phase output", result.stdout)
        evidence = result.stdout.split("==> SteamCMD failure evidence", 1)[1]
        self.assertIn("Assertion Failed: mock SteamCMD failure", evidence)
        self.assertNotIn("phase=bootstrap complete", result.stdout)

    def test_first_assertion_survives_verbose_progress_and_minidump_logs(self):
        result = self.run_phase("bootstrap", "bootstrap_noisy_crash")
        self.assertEqual(result.returncode, 254)
        self.assertEqual(self.count("bootstrap"), 1)
        tail = "\n".join(result.stdout.splitlines()[-80:])
        self.assertIn("==> SteamCMD failure evidence", tail)
        self.assertIn("Assertion Failed: mock failure hidden by progress", tail)
        self.assertNotIn("phase=bootstrap complete", result.stdout)

    def test_remote_build_rejects_affected_docker_engine_before_building(self):
        source = (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text()
        preflight = source[source.index("echo 'FAMINE_BUILD_STARTED'"):
                           source.index('# Do not let a second build')]
        docker = self.root / "tools/docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$DST_TEST_DOCKER_SERVER_VERSION"\n')
        docker.chmod(0o700)
        for version in ["29.4.2", "29.4.2-1", "29.4.2+vendor", "29.4.3", "29.9.1", "29.9.2", ""]:
            with self.subTest(version=version):
                result = subprocess.run(["bash", "-euc", preflight], cwd=self.root,
                    env=dict(self.environment, DST_TEST_DOCKER_SERVER_VERSION=version),
                    capture_output=True, text=True, timeout=10, check=False)
                affected = version == "" or version.startswith(("29.4.2-", "29.4.2+")) or version == "29.4.2"
                self.assertEqual(result.returncode, 1 if affected else 0, result.stderr)
                self.assertIn("FAMINE_BUILD_STARTED", result.stdout)
                if affected:
                    self.assertIn("FAMINE_BUILD_FAILED:1", result.stderr)
                    self.assertIn("DOCKER_SERVER_VERSION_UNAVAILABLE" if not version
                                  else "DOCKER_ENGINE_INCOMPATIBLE", result.stderr)
                else:
                    self.assertNotIn("FAMINE_BUILD_FAILED", result.stderr)
                    self.assertIn("Docker Engine server=" + version, result.stdout)

    def test_both_build_routes_use_the_same_installer_and_two_separate_layers(self):
        source = (PROJECT / "docker/runtime/Dockerfile").read_text()
        self.assertIn("COPY --chown=steam:steam install-dst.sh /home/steam/install-dst.sh", source)
        self.assertIn("RUN bash ./install-dst.sh bootstrap\nRUN bash ./install-dst.sh download", source)
        self.assertIn("COPY --from=builder --chown=famine:famine /home/steam/steamcmd.sh", source)
        self.assertIn("/usr/local/bin/famine-workshop-fetch", source)
        self.assertIn("steamcmd-workshop-v1", source)
        self.assertIn("libcurl4-gnutls-dev:i386", source)
        remote = (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text()
        self.assertIn("Dockerfile.build-base", remote)
        self.assertIn("Dockerfile.runtime-base", remote)
        self.assertIn("install-dst.sh download", remote)
        self.assertIn("DST_ROOT", remote)
        self.assertIn(".staging-", remote)
        build_base = (PROJECT / "docker/runtime/Dockerfile.build-base").read_text()
        self.assertIn("RUN bash ./install-dst.sh bootstrap", build_base)
        self.assertIn("COPY install-dst.sh /home/steam/install-dst.sh", build_base)
        runtime_base = (PROJECT / "docker/runtime/Dockerfile.runtime-base").read_text()
        self.assertIn("/usr/local/bin/famine-workshop-fetch", runtime_base)
        self.assertIn("steamcmd-workshop-v1", runtime_base)
        self.assertIn("COPY --from=steamcmd /home/steam/steamcmd.sh", runtime_base)
        fetcher = (PROJECT / "docker/runtime/fetch-workshop.sh").read_text()
        self.assertIn("+workshop_download_item 322330", fetcher)
        self.assertIn('"$home_root/Steam/steamapps/workshop/content/322330/$workshop_id"', fetcher)
        self.assertIn('"$home_root/.local/share/Steam/steamapps/workshop/content/322330/$workshop_id"', fetcher)
        self.assertIn('output_root="$work_root/output"', fetcher)
        self.assertIn("WORKSHOP_CONTENT_INVALID", fetcher)
        transport = (PROJECT / "src-tauri/src/image_build.rs").read_text()
        self.assertIn('include_str!("../../docker/runtime/Dockerfile")', transport)
        self.assertIn('include_str!("../../docker/runtime/Dockerfile.build-base")', transport)
        self.assertIn('include_str!("../../docker/runtime/Dockerfile.runtime-base")', transport)
        self.assertIn('include_str!("../../docker/runtime/install-dst.sh")', transport)
        self.assertIn('include_str!("../../docker/runtime/fetch-workshop.sh")', transport)
        self.assertIn('include_str!("../../docker/runtime/prepare-base-image.sh")', transport)
        self.assertIn('{script_dir}/Dockerfile', transport)
        self.assertIn('{script_dir}/Dockerfile.build-base', transport)
        self.assertIn('{script_dir}/Dockerfile.runtime-base', transport)
        self.assertIn('{script_dir}/fetch-workshop.sh', transport)
        self.assertIn('{script_dir}/install-dst.sh', transport)
        self.assertIn('/opt/famine-hub/scripts/image-build/{release_id}', transport)

    def test_release_build_uses_the_daemon_builder_and_shared_dockerfile(self):
        source = (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text()
        self.assertIn("DOCKER_ENGINE_INCOMPATIBLE", source)
        self.assertIn("29.4.2", source)
        self.assertIn("29.4.3 or newer", source)
        self.assertIn('RUNTIME_IMAGE_NAME="hungerdesolate/dst-runtime-base"', source)
        self.assertIn('DST_ROOT="/var/lib/famine-hub/dst"', source)
        self.assertIn("DST_CONTENT_DIGEST", source)
        self.assertIn('bash "${SCRIPT_DIR}/actions/prepare-base-image.sh"', source)
        self.assertIn("DOCKER_BUILDKIT=1 docker build", source)
        self.assertIn("--platform linux/amd64", source)
        self.assertIn("--progress=plain", source)
        self.assertNotIn("buildx", source)
        self.assertNotIn("configure-buildkit-mirrors.py", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
