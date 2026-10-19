"""Temporary loopback SSH fixture; never modifies the user's SSH configuration."""
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


def main():
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    keygen = shutil.which("ssh-keygen")
    if not Path(sshd).is_file() or not keygen:
        raise SystemExit("Local OpenSSH tools are required for this optional test")
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="hunger-ssh-") as directory:
        root = Path(directory)
        for name in ["host", "client"]:
            subprocess.run([keygen, "-q", "-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-f", str(root / name)], check=True)
        (root / "authorized_keys").write_bytes((root / "client.pub").read_bytes())
        managed = root / "managed"
        managed.mkdir()
        (managed / "host.json").write_text(json.dumps({"schemaVersion": 1, "hostId": "11111111-1111-4111-8111-111111111111"}))
        shutil.copyfile(project / "src-tauri/tests/fixtures/registry-v1.json", managed / "registry.json")
        instance = managed / "instances/22222222-2222-4222-8222-222222222222"
        instance.mkdir(parents=True)
        shutil.copyfile(project / "src-tauri/tests/fixtures/manifest-v1.json", instance / "manifest.json")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        username = getpass.getuser()
        if not username or any(char.isspace() for char in username):
            raise SystemExit("Invalid local test account")
        config = root / "sshd_config"
        config.write_text(f"Port {port}\nListenAddress 127.0.0.1\nHostKey {root / 'host'}\nPidFile {root / 'sshd.pid'}\nAuthorizedKeysFile {root / 'authorized_keys'}\nStrictModes no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nAllowUsers {username}\nSubsystem sftp internal-sftp\nLogLevel ERROR\n")
        log = root / "sshd.log"
        with log.open("wb") as output:
            daemon = subprocess.Popen([sshd, "-D", "-e", "-f", str(config)], stdout=output, stderr=output)
            try:
                ready = False
                for _ in range(40):
                    if daemon.poll() is not None:
                        raise RuntimeError("Isolated SSH daemon could not start: " + log.read_text())
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            ready = True
                            break
                    except OSError:
                        time.sleep(0.1)
                if not ready:
                    raise RuntimeError("Isolated SSH daemon did not become ready")
                env = {**os.environ, "HUNGER_SSH_TEST_PORT": str(port), "HUNGER_SSH_TEST_USER": username, "HUNGER_SSH_TEST_KEY": str(root / "client"), "HUNGER_REGISTRY_TEST_ROOT": str(managed)}
                result = subprocess.run(["cargo", "test", "--manifest-path", "src-tauri/Cargo.toml", "--offline", "local_ssh_fixture_authenticates_only_after_trust", "--", "--ignored"], cwd=project, env=env)
                if result.returncode:
                    print(log.read_text())
                    raise SystemExit(result.returncode)
            finally:
                daemon.terminate()
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait()


if __name__ == "__main__":
    main()
