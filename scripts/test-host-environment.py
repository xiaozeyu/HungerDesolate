"""Exercise the production probe in isolated directories with deterministic command fixtures."""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


def executable(path, text):
    path.write_text("#!/bin/bash\nset -eu\n" + text)
    path.chmod(0o700)


def main():
    project = Path(__file__).resolve().parents[1]
    original = (project / "remote-scripts/probes/host-environment.sh").read_text()
    with tempfile.TemporaryDirectory(prefix="hdenv-", dir="/tmp") as directory:
        root = Path(directory)
        reports = root / "reports"
        reports.mkdir()
        cases = ["available", "missing", "pythonMissing", "permissionDenied", "unavailable", "badServer", "composeMissing", "timeoutMissing", "invalidRoot"]
        for name in cases:
            case = root / name
            binary = case / "bin"
            binary.mkdir(parents=True)
            manager = case / "manager"
            docker_root = case / "docker data"
            docker_root.mkdir()
            if name == "invalidRoot":
                manager.write_text("not a directory")
            else:
                manager.mkdir()
            for tool in ["bash", "base64", "tr", "head", "python3"]:
                if tool == "python3" and name == "pythonMissing":
                    continue
                source = shutil.which(tool)
                if not source:
                    raise RuntimeError(f"Missing local test tool: {tool}")
                (binary / tool).symlink_to(source)
            executable(binary / "uname", 'if [[ "$1" == -s ]]; then echo Linux; else echo x86_64; fi\n')
            executable(binary / "id", "echo 1000\n")
            executable(binary / "df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 100000 2000 98000 2%% /\\n'\n")
            for tool in ["tar", "gzip", "sha256sum", "flock", "jq"]:
                executable(binary / tool, "exit 0\n")
            if name != "timeoutMissing":
                executable(binary / "timeout", 'shift 3\nexec "$@"\n')
            if name != "missing":
                executable(binary / "docker", f"""
[[ -z "${{DOCKER_HOST+x}}" && -z "${{DOCKER_CONTEXT+x}}" ]] || exit 9
case "$*" in
  *--version*) echo 'Docker version 27.1.0, build fixture';;
  *'compose version'*) {'exit 1' if name == 'composeMissing' else 'echo 2.29.0'};;
  *'version --format'*) {'exit 1' if name == 'unavailable' else 'echo invalid-json' if name == 'badServer' else "echo '{\"Version\":\"27.1.0\",\"Os\":\"linux\",\"Arch\":\"amd64\"}'"};;
  *'info --format'*) printf '%s\\n' '{docker_root}';;
  *) exit 2;;
esac
""")
            socket_path = case / "docker.sock"
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(socket_path))
                if name == "permissionDenied":
                    socket_path.chmod(0)
                probe = original.replace(
                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    f"PATH={binary}", 1,
                ).replace("PYTHON3=/usr/bin/python3", f"PYTHON3={binary / 'python3'}", 1).replace(
                    "DOCKER_SOCKET=/var/run/docker.sock", f"DOCKER_SOCKET={socket_path}", 1,
                ).replace(
                    "MANAGEMENT_ROOT=/var/lib/famine-hub", f"MANAGEMENT_ROOT={manager}", 1,
                )
                result = subprocess.run(["/bin/bash", "-s"], input=probe, capture_output=True, text=True, timeout=10,
                    env={**os.environ, "DOCKER_HOST": "tcp://example.invalid:2375", "DOCKER_CONTEXT": "unrelated-fixture-context"})
            if result.returncode:
                raise RuntimeError(f"{name}: probe exit {result.returncode}: {result.stderr}")
            lines = result.stdout.splitlines()
            assert lines[0] == "FHENV\t1" and lines[-1] == "FHENV\tEND", name
            fields = {}
            for line in lines[1:-1]:
                key, state, payload = line.split("\t")
                assert key not in fields, (name, key)
                fields[key] = (state, base64.b64decode(payload, validate=True).decode())
            assert len(fields) == 28, name
            if name == "missing":
                assert fields["dockerClient"][0] == "missing"
            elif name == "pythonMissing":
                assert fields["tool.python3"][0] == "missing"
            elif name == "permissionDenied":
                assert fields["dockerServer"][0] == "permissionDenied"
            elif name == "timeoutMissing":
                assert fields["tool.timeout"][0] == "missing" and fields["dockerServer"][0] == "unavailable"
            elif name == "unavailable":
                assert fields["dockerServer"][0] == "failed"
            elif name == "composeMissing":
                assert fields["compose"][0] == "failed"
            elif name == "invalidRoot":
                assert fields["managementRoot"][0] == "invalid"
            elif name == "available":
                assert json.loads(fields["dockerServer"][1])["Arch"] == "amd64"
            (reports / f"{name}.out").write_text(result.stdout)
            print(f"{name}: production shell probe passed", flush=True)
        env = {**os.environ, "HUNGER_ENV_FIXTURE_DIR": str(reports)}
        subprocess.run([
            "cargo", "test", "--manifest-path", "src-tauri/Cargo.toml", "--offline",
            "isolated_probe_fixture_files", "--", "--ignored",
        ], cwd=project, env=env, check=True)


if __name__ == "__main__":
    main()
