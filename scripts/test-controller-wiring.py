"""Controller wiring evidence for all four heavy task types."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "remote-scripts" / "actions"
SCRIPTS = ["mods-apply.py", "backup-task.py", "restore-task.py", "host-mods-download.py"]


class ControllerWiringTests(unittest.TestCase):
    def test_all_heavy_tasks_share_controller_queue_and_lease(self):
        for name in SCRIPTS:
            source = (ROOT / name).read_text()
            self.assertIn("def controller_main()", source, name)
            self.assertIn("FAMINE_CONTROLLER_LEASE", source, name)
            self.assertIn("queue-controller.py", source, name)
            self.assertIn('"modApply": ROOT / "mod-apply-tasks"', source, name)
            self.assertIn('"backupCreate": ROOT / "backup-tasks"', source, name)
            self.assertIn('"backupRestore": ROOT / "restore-tasks"', source, name)
            self.assertIn('"hostModPrepare": ROOT / "host-mod-tasks"', source, name)
            self.assertIn('argv[1] == "controller"', source, name)


if __name__ == "__main__":
    unittest.main()
