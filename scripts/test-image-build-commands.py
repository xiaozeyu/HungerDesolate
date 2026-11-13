"""Execute build scripts against isolated CLI fixtures; never run Docker or sudo."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
RELEASE_ID = "11111111-1111-4111-8111-111111111111"
IMAGE_ID = "sha256:" + "a" * 64

DOCKER = r'''
import json, os, sys
from pathlib import Path
root = Path(os.environ["BUILD_TEST_ROOT"])
args = sys.argv[1:]
with (root / "commands.jsonl").open("a") as output:
    output.write(json.dumps({"args": args, "host": os.environ.get("DOCKER_HOST"),
                             "context": os.environ.get("DOCKER_CONTEXT")}) + "\n")
failure = os.environ.get("BUILD_TEST_FAILURE", "")
if args[:1] == ["version"]:
    print("29.9.2")
elif args[:2] in (["image", "ls"], ["image", "prune"], ["builder", "prune"]):
    pass
elif args[:2] == ["image", "inspect"]:
    image = args[2]
    fmt = args[args.index("--format") + 1] if "--format" in args else ""
    if "Os" in fmt or "Architecture" in fmt:
        if image == "debian:12-slim" and (os.environ.get("BUILD_TEST_PRELOADED") == "1"
                                          or (root / "base-tagged").exists()):
            print("linux/amd64")
            sys.exit(0)
        sys.exit(1)
    if "dst-build-base" in image or "dst-runtime-base" in image:
        if failure == "image_id":
            print("invalid")
            sys.exit(0)
        built = (root / "built-tags.jsonl").read_text().splitlines() if (root / "built-tags.jsonl").exists() else []
        if os.environ.get("BUILD_TEST_BASES_PRELOADED") == "1" or image in built:
            print("sha256:" + "a" * 64)
            sys.exit(0)
        sys.exit(1)
    if failure == "image_id":
        print("invalid")
        sys.exit(0)
    print("sha256:" + "a" * 64)
elif args[:1] == ["pull"]:
    if failure == "base":
        sys.exit(19)
    (root / "base-pulled").write_text(args[-1])
elif args[:1] == ["tag"]:
    (root / "base-tagged").write_text(args[1])
elif args[:1] == ["build"]:
    if failure == "build":
        sys.exit(19)
    tag = args[args.index("--tag") + 1]
    with (root / "built-tags.jsonl").open("a") as output:
        output.write(tag + "\n")
    (root / "built").write_text("1")
elif args[:1] == ["run"]:
    if any("install-dst.sh" in value or "steamcmd.sh" in value for value in args):
        if failure == "download":
            sys.exit(19)
        if failure == "version_read":
            sys.exit(23)
        volume = args[args.index("-v") + 1]
        host = Path(volume.split(":")[0])
        (host / "bin64").mkdir(parents=True, exist_ok=True)
        binary = host / "bin64/dontstarve_dedicated_server_nullrenderer_x64"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        (host / "version.txt").write_text("unknown\n" if failure == "version_format" else "756039\n")
        (host / "steamapps").mkdir(parents=True, exist_ok=True)
        (host / "steamapps/appmanifest_343050.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"343050"\n\t"buildid"\t\t"26474880"\n}\n')
        (host / "data/databundles").mkdir(parents=True, exist_ok=True)
        (host / "data/databundles/scripts.zip").write_bytes(b"scripts")
    elif args[-1] == "cat /srv/dst/version.txt":
        if failure == "version_read": sys.exit(23)
        print("unknown" if failure == "version_format" else "756039")
    elif args[-1] == "cat /srv/dst/steamapps/appmanifest_343050.acf":
        print('"AppState"\n{\n\t"appid"\t\t"343050"\n\t"buildid"\t\t"26474880"\n}')
    elif "famine-workshop-fetch" in args[-1]:
        sys.exit(23 if failure == "capability" else 0)
    else:
        sys.exit("unexpected runtime validation command")
elif args[:1] == ["save"]:
    Path(args[args.index("-o") + 1]).write_bytes(b"fixture image artifact")
else:
    sys.exit("unexpected docker command: " + repr(args))
'''


class ImageBuildCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="image-build-commands-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bins = self.root / "tools"
        self.bins.mkdir()
        self.scripts = self.root / "scripts"
        (self.scripts / "actions").mkdir(parents=True)
        for name in ["install-dst.sh", "fetch-workshop.sh", "Dockerfile", "Dockerfile.build-base",
                     "Dockerfile.runtime-base", "prepare-base-image.sh"]:
            shutil.copyfile(PROJECT / "docker/runtime" / name, self.scripts / "actions" / name)
        self.python(self.bins / "docker", DOCKER)
        self.python(self.bins / "flock", "import fcntl, sys\n"
                    "try: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                    "except BlockingIOError: sys.exit(1)\n")
        self.python(self.bins / "df", "import os\n"
                    "print('Filesystem 1024-blocks Used Available Capacity Mounted on')\n"
                    "print('/dev/fake 999999999 0 ' + os.environ.get('BUILD_TEST_FREE_KB', '999999999') + ' 0% /')\n")
        self.python(self.bins / "chown", "import sys\nsys.exit(0)\n")
        self.executable(self.bins / "sudo", '#!/bin/sh\nif [ "$1" = chown ]; then exit 0; fi\nexec "$@"\n')
        self.python(self.bins / "sha256sum", "import hashlib, sys\nfrom pathlib import Path\n"
                    "data = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else sys.stdin.buffer.read()\n"
                    "print(hashlib.sha256(data).hexdigest(), sys.argv[1] if len(sys.argv) > 1 else '-')\n")
        self.python(self.bins / "jq", "import json, re, sys\nfrom pathlib import Path\n"
                    "data=json.loads(Path(sys.argv[2]).read_text())\n"
                    "data['artifactDigest']=re.search(r'sha256:[0-9a-f]{64}', sys.argv[1])[0]\n"
                    "print(json.dumps(data))\n")
        self.python(self.scripts / "actions/register-built-release.py",
                    "import json, os, sys\nfrom pathlib import Path\n"
                    "root=Path(os.environ['BUILD_TEST_ROOT'])\n"
                    "metadata=root/'managed/staging'/sys.argv[1]/'release-metadata.json'\n"
                    "data=json.loads(metadata.read_text())\n"
                    "assert data['imageDigest']=='sha256:'+'a'*64\n"
                    "assert data['dstBuild']=='756039'\n"
                    "assert data['steamBuildId']=='26474880'\n"
                    "(root/'registered').write_text(sys.argv[1])\n")
        self.context = self.root / "context"
        self.context.mkdir()
        for name in ["Dockerfile", "prepare-base-image.sh"]:
            shutil.copyfile(PROJECT / "docker/runtime" / name, self.context / name)
        source = (PROJECT / "remote-scripts/actions/build-dst-image.sh").read_text()
        source = source.replace('/opt/famine-hub/scripts', str(self.scripts))
        source = source.replace('/var/lib/famine-hub', str(self.root / "managed"))
        self.remote = self.scripts / "actions/build-dst-image.sh"
        self.remote.write_text(source)
        local_source = (PROJECT / "docker/runtime/build.sh").read_text()
        self.local = self.context / "build.sh"
        self.local.write_text(local_source)

    def executable(self, path, content):
        path.write_text(content)
        path.chmod(0o700)

    def python(self, path, content):
        self.executable(path, "#!" + sys.executable + "\n" + content)

    def run_build(self, route, failure="", **overrides):
        (self.root / "commands.jsonl").write_text("")
        for name in ("base-tagged", "base-pulled", "built", "registered", "built-tags.jsonl"):
            (self.root / name).unlink(missing_ok=True)
        (self.context / "release-metadata.json").unlink(missing_ok=True)
        shutil.rmtree(self.root / "managed/staging", ignore_errors=True)
        env = {**os.environ, "PATH": str(self.bins) + os.pathsep + os.environ["PATH"],
               "BUILD_TEST_ROOT": str(self.root), "BUILD_TEST_FAILURE": failure,
               "BUILD_TEST_PRELOADED": "0", "BUILD_TEST_BASES_PRELOADED": "0",
               "RELEASE_ID": RELEASE_ID, "IMAGE_NAME": "hungerdesolate/dst-runtime",
               "BUILD_TAG": "release-" + RELEASE_ID, "PLATFORM": "linux/amd64",
               "STEAMCMD_BOOTSTRAP_DEADLINE": "4800s", "STEAMCMD_DOWNLOAD_DEADLINE": "7200s",
               "DOCKER_HOST": "tcp://wrong-daemon.invalid:2375", "DOCKER_CONTEXT": "wrong-context",
               **overrides}
        command = ["bash", str(self.remote), RELEASE_ID] if route == "remote" else ["bash", str(self.local)]
        result = subprocess.run(command, cwd=self.context, env=env,
                                capture_output=True, text=True, timeout=20, check=False)
        calls = [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]
        return result, calls

    def test_remote_build_uses_shared_dockerfile_and_pulls_base_image(self):
        result, calls = self.run_build("remote")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        builds = [item["args"] for item in calls if item["args"][:1] == ["build"]]
        self.assertEqual(len(builds), 2)
        for build in builds:
            self.assertIn("--platform", build)
            self.assertEqual(build[build.index("--platform") + 1], "linux/amd64")
            self.assertIn("--progress=plain", build)
            self.assertNotIn("--load", build)
        self.assertNotIn("buildx", [item["args"][0] for item in calls])
        self.assertTrue(all(item["host"] == "unix:///var/run/docker.sock" and item["context"] is None for item in calls))
        pull = next(item["args"] for item in calls if item["args"][:1] == ["pull"])
        self.assertIn("--platform", pull)
        self.assertIn("linux/amd64", pull)
        self.assertTrue(pull[-1].endswith("/library/debian:12-slim"))
        self.assertIn("/library/debian:12-slim", pull[-1])
        tag = next(item["args"] for item in calls if item["args"][:1] == ["tag"])
        self.assertEqual(tag[-1], "debian:12-slim")
        for item in calls:
            if item["args"][:1] == ["run"] and not any("install-dst.sh" in value or "steamcmd.sh" in value for value in item["args"]):
                self.assertIn(IMAGE_ID, item["args"])
                self.assertIn("none", item["args"])
        self.assertEqual((self.root / "registered").read_text(), RELEASE_ID)
        metadata = json.loads((self.root / "managed/staging" / RELEASE_ID / "release-metadata.json").read_text())
        self.assertEqual(metadata["image"], "hungerdesolate/dst-runtime-base")
        self.assertTrue(metadata["artifactDigest"].startswith("sha256:"))
        self.assertNotEqual(metadata["artifactDigest"], metadata["imageDigest"])
        dst_dir = self.root / "managed/dst" / RELEASE_ID
        self.assertEqual((dst_dir / "version.txt").read_text().strip(), "756039")
        self.assertIn("FAMINE_BUILD_COMPLETED", result.stdout)
        self.assertIn("==> 清理构建缓存", result.stdout)
        self.assertFalse((self.root / "managed/image-build/dst-runtime-build").exists())
        self.assertFalse((self.root / "managed/dst" / (".staging-" + RELEASE_ID)).exists())
        self.assertTrue(any(item["args"][:3] == ["builder", "prune", "--all"] for item in calls))
        self.assertFalse(any(item["args"][:2] == ["image", "prune"] for item in calls))

    def test_reuses_latest_dst_directory_as_incremental_baseline(self):
        base = self.root / "managed/dst/base"
        base.mkdir(parents=True)
        (base / "version.txt").write_text("700000\n")
        result, _ = self.run_build("remote")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("增量基线", result.stdout)
        self.assertTrue((self.root / "managed/dst" / RELEASE_ID / "version.txt").is_file())
        self.assertTrue(base.is_dir())

    def test_insufficient_space_stops_before_pull_or_build(self):
        result, calls = self.run_build("remote", BUILD_TEST_FREE_KB="1024")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("IMAGE_BUILD_SPACE_INSUFFICIENT", result.stderr)
        self.assertNotIn("FAMINE_BUILD_COMPLETED", result.stdout)
        self.assertFalse(any(item["args"][:1] in (["pull"], ["build"]) for item in calls))
        self.assertTrue(any(item["args"][:3] == ["builder", "prune", "--all"] for item in calls))

    def test_failed_build_still_cleans_cache(self):
        result, calls = self.run_build("remote", "build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAMINE_BUILD_FAILED:", result.stderr)
        self.assertFalse((self.root / "managed/image-build/dst-runtime-build").exists())
        self.assertTrue(any(item["args"][:3] == ["builder", "prune", "--all"] for item in calls))
        self.assertFalse(any(item["args"][:2] == ["image", "prune"] for item in calls))

    def test_local_build_uses_the_same_base_image_and_exports_fixed_image(self):
        result, calls = self.run_build("local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.context / "release-metadata.json").read_text())
        self.assertEqual(metadata["imageDigest"], IMAGE_ID)
        self.assertEqual(metadata["dstBuild"], "756039")
        self.assertFalse(metadata["verified"])
        saved = next(item["args"] for item in calls if item["args"][:1] == ["save"])
        self.assertEqual(saved[1], IMAGE_ID)
        self.assertTrue((self.context / saved[saved.index("-o") + 1]).is_file())
        build = next(item["args"] for item in calls if item["args"][:1] == ["build"])
        self.assertIn("STEAMCMD_BOOTSTRAP_DEADLINE=4800s", build)
        self.assertNotIn("buildx", [item["args"][0] for item in calls])

    def test_cached_amd64_base_image_skips_the_mirror_pull(self):
        for route in ["local", "remote"]:
            with self.subTest(route=route):
                result, calls = self.run_build(route, BUILD_TEST_PRELOADED="1")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(any(item["args"][:1] == ["pull"] for item in calls))
                self.assertTrue(any(item["args"][:1] == ["build"] for item in calls))

    def test_base_image_failure_stops_before_docker_build(self):
        result, calls = self.run_build("remote", "base")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BASE_IMAGE_UNAVAILABLE", result.stderr)
        self.assertIn("FAMINE_BUILD_FAILED:", result.stderr)
        self.assertFalse(any(item["args"][:1] == ["build"] for item in calls))
        self.assertFalse((self.root / "registered").exists())

    def test_failed_build_or_artifact_checks_never_register_a_release(self):
        for failure in ["base", "build", "download", "image_id", "version_read", "version_format", "capability"]:
            with self.subTest(failure=failure):
                result, calls = self.run_build("remote", failure)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.root / "registered").exists())
                self.assertFalse((self.root / "managed/staging" / RELEASE_ID / "release-metadata.json").exists())
                self.assertNotIn("FAMINE_BUILD_COMPLETED", result.stdout)
                self.assertIn("FAMINE_BUILD_FAILED:", result.stderr)

    def test_local_invalid_image_or_version_never_exports_metadata(self):
        for failure in ["base", "image_id", "version_read", "version_format"]:
            with self.subTest(failure=failure):
                result, _ = self.run_build("local", failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.context / "release-metadata.json").exists())

    def test_second_build_does_not_remove_the_active_build_context(self):
        build = self.root / "managed/image-build"
        build.mkdir(parents=True)
        active = build / "dst-runtime-build"
        active.mkdir()
        sentinel = active / "install-dst.sh"
        sentinel.write_text("active build must remain intact")
        with (build / "build.lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, calls = self.run_build("remote")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("IMAGE_BUILD_ALREADY_RUNNING", result.stderr)
        self.assertEqual(sentinel.read_text(), "active build must remain intact")
        self.assertFalse(any(item["args"][:1] == ["build"] for item in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
