import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "configure_buildkit_mirrors", PROJECT / "remote-scripts/actions/configure-buildkit-mirrors.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildKitMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buildkit-mirrors-")
        root = Path(self.temp.name)
        self.daemon = root / "daemon.json"
        self.buildkit = root / "buildkitd.toml"

    def tearDown(self):
        self.temp.cleanup()

    def test_uses_existing_mirrors_and_preserves_other_daemon_settings(self):
        self.daemon.write_text(json.dumps({
            "log-level": "warn",
            "features": {"containerd-snapshotter": True},
            "registry-mirrors": ["https://mirror.example.test"],
        }))

        result = MODULE.configure(self.daemon, self.buildkit)

        self.assertEqual(result, "configured")
        daemon = json.loads(self.daemon.read_text())
        self.assertEqual(daemon["log-level"], "warn")
        self.assertEqual(daemon["features"], {"containerd-snapshotter": True})
        config = tomllib.loads(self.buildkit.read_text())
        self.assertEqual(config['registry']["docker.io"]["mirrors"], ["https://mirror.example.test"])

    def test_adds_default_mirrors_without_replacing_existing_settings(self):
        self.daemon.write_text(json.dumps({"log-level": "debug"}))

        result = MODULE.configure(self.daemon, self.buildkit)

        self.assertEqual(result, "daemon-updated")
        daemon = json.loads(self.daemon.read_text())
        self.assertEqual(daemon["log-level"], "debug")
        self.assertEqual(daemon["registry-mirrors"], MODULE.DEFAULT_MIRRORS)
        self.assertEqual(tomllib.loads(self.buildkit.read_text())['registry']["docker.io"]["mirrors"],
                         MODULE.DEFAULT_MIRRORS)

    def test_rejects_malformed_daemon_json_without_overwriting_it(self):
        self.daemon.write_text("{invalid")

        with self.assertRaises(json.JSONDecodeError):
            MODULE.configure(self.daemon, self.buildkit)

        self.assertEqual(self.daemon.read_text(), "{invalid")
        self.assertFalse(self.buildkit.exists())

    def test_rejects_invalid_mirror_urls(self):
        self.daemon.write_text(json.dumps({"registry-mirrors": ["file:///tmp/registry"]}))

        with self.assertRaises(ValueError):
            MODULE.configure(self.daemon, self.buildkit)

        self.assertFalse(self.buildkit.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
