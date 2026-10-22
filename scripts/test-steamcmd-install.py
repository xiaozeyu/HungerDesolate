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
        self.environment = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ["PATH"])

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
        self.assertIn("600s", (self.root / "timeout.log").read_text())
        self.assertIn("1800s", (self.root / "timeout.log").read_text())

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

    def test_both_build_routes_use_the_same_installer_and_two_separate_layers(self):
        for path in [PROJECT / "docker/runtime/Dockerfile", PROJECT / "remote-scripts/actions/build-dst-image.sh"]:
            source = path.read_text()
            self.assertIn("COPY --chown=steam:steam install-dst.sh /home/steam/install-dst.sh", source)
            self.assertIn("RUN bash ./install-dst.sh bootstrap\nRUN bash ./install-dst.sh download", source)
            self.assertIn("COPY --from=builder --chown=famine:famine /home/steam/steamcmd.sh", source)
            self.assertIn("/usr/local/bin/famine-workshop-fetch", source)
            self.assertIn("steamcmd-workshop-v1", source)
        self.assertIn('fetch-workshop.sh" ./fetch-workshop.sh',
                      (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text())
        fetcher = (PROJECT / "docker/runtime/fetch-workshop.sh").read_text()
        self.assertIn("+workshop_download_item 322330", fetcher)
        self.assertIn('"$home_root/Steam/steamapps/workshop/content/322330/$workshop_id"', fetcher)
        self.assertIn('"$home_root/.local/share/Steam/steamapps/workshop/content/322330/$workshop_id"', fetcher)
        self.assertIn('output_root="$work_root/output"', fetcher)
        self.assertIn("WORKSHOP_CONTENT_INVALID", fetcher)
        transport = (PROJECT / "src-tauri/src/image_build.rs").read_text()
        self.assertIn('include_str!("../../docker/runtime/install-dst.sh")', transport)
        self.assertIn('include_str!("../../docker/runtime/fetch-workshop.sh")', transport)
        self.assertIn('scripts/actions/fetch-workshop.sh', transport)
        self.assertIn('/opt/famine-hub/scripts/actions/install-dst.sh', transport)

    def test_release_build_uses_unique_image_tag_and_scoped_cache_cleanup(self):
        source = (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text()
        self.assertIn('BUILD_TAG="release-${RELEASE_ID}"', source)
        self.assertIn("docker buildx create --name \"$BUILDER_NAME\"", source)
        self.assertIn('--buildkitd-config "$BUILDKIT_CONFIG"', source)
        self.assertIn('configure-buildkit-mirrors.py', source)
        self.assertIn('docker buildx build \\\n    --builder "$BUILDER_NAME"', source)
        self.assertIn('docker buildx prune --builder "$BUILDER_NAME" --all --force', source)
        self.assertIn('docker buildx rm --force "$BUILDER_NAME"', source)
        self.assertNotIn("docker builder prune", source)
        transport = (PROJECT / "src-tauri/src/image_build.rs").read_text()
        self.assertIn('include_str!("../../remote-scripts/actions/configure-buildkit-mirrors.py")', transport)


if __name__ == "__main__":
    unittest.main(verbosity=2)
