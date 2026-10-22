import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT / "docker/runtime/fetch-workshop.sh"
WORKSHOP_ID = "2893991859"


class WorkshopFetchTests(unittest.TestCase):
    def run_wrapper(self, cache_layout):
        temporary = tempfile.TemporaryDirectory(prefix="workshop-fetch-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        work = root / "work"
        source = root / "image-steamcmd"
        fake_bin = root / "bin"
        (source / "linux32").mkdir(parents=True)
        fake_bin.mkdir()
        steamcmd = source / "steamcmd.sh"
        steamcmd.write_text("#!/bin/sh\n"
                            "set -eu\n"
                            f"target=\"$HOME/{cache_layout}/steamapps/workshop/content/322330/{WORKSHOP_ID}\"\n"
                            "mkdir -p \"$target/mod-data\"\n"
                            "printf 'configuration_options = {}\\n' > \"$target/mod-data/modinfo.lua\"\n"
                            "printf 'content' > \"$target/mod-data/assets.bin\"\n")
        steamcmd.chmod(0o755)
        linuxcmd = source / "linux32/steamcmd"
        linuxcmd.write_text("#!/bin/sh\nexit 0\n")
        linuxcmd.chmod(0o755)
        timeout = fake_bin / "timeout"
        timeout.write_text("#!/bin/sh\nshift 3\nexec \"$@\"\n")
        timeout.chmod(0o755)
        find = fake_bin / "find"
        find.write_text(f"#!{sys.executable}\n"
                        "import os, pathlib, sys\n"
                        "root = pathlib.Path(sys.argv[1])\n"
                        "for current, dirs, files in os.walk(root, followlinks=False):\n"
                        "    relative = pathlib.Path(current).relative_to(root)\n"
                        "    if len(relative.parts) >= 8: dirs[:] = []\n"
                        "    if 'modinfo.lua' in files:\n"
                        "        print(pathlib.Path(current) / 'modinfo.lua')\n"
                        "        break\n")
        find.chmod(0o755)

        script = root / "fetch-workshop.sh"
        script.write_text(WRAPPER.read_text().replace("work_root=/work\n", f"work_root={work}\n").replace(
            "/opt/famine/steamcmd", str(source)))
        script.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        result = subprocess.run([str(script), WORKSHOP_ID], text=True, capture_output=True,
                                check=False, env=env)
        return result, work

    def test_finds_workshop_content_under_home_steam_cache(self):
        result, work = self.run_wrapper("Steam")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout + str(list(work.rglob("*"))))
        self.assertTrue((work / "output" / WORKSHOP_ID / "modinfo.lua").is_file())
        self.assertTrue((work / "output" / WORKSHOP_ID / "assets.bin").is_file())

    def test_finds_nested_modinfo_under_local_steam_cache(self):
        result, work = self.run_wrapper(".local/share/Steam")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout + str(list(work.rglob("*"))))
        self.assertEqual((work / "output" / WORKSHOP_ID / "modinfo.lua").read_text(),
                         "configuration_options = {}\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
