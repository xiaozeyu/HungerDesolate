"""Test deterministic rendering of the DST cluster and shard configuration."""
import importlib.util
import json
from pathlib import Path
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "world_config_render", PROJECT / "remote-scripts/actions/world-config-render.py"
)
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)
CONFIG = importlib.util.spec_from_file_location(
    "world_config", PROJECT / "remote-scripts/actions/world-config.py"
)
WORLD = importlib.util.module_from_spec(CONFIG)
CONFIG.loader.exec_module(WORLD)
WORLD.OPTION_SCHEMA = json.loads((PROJECT / "src/lib/dst-world-options.json").read_text())


class WorldConfigRenderTests(unittest.TestCase):
    def settings(self):
        return WORLD.default_settings(True, "荒野一服")

    def ports(self):
        return [
            {"shard": shard, "purpose": purpose, "containerPort": port}
            for shard, offset in (("master", 0), ("caves", 1))
            for purpose, port in (("game", 11000 + offset), ("steamAuth", 8766 + offset),
                                  ("steamMaster", 27016 + offset))
        ]

    def test_render_generates_deterministic_files_and_both_shards(self):
        settings = self.settings()
        settings["worldgen"]["master"]["worldgenOverrides"]["grass"] = "often"
        first = RENDER.render(settings, b"token-value", b"server-password", "a" * 32, self.ports())
        second = RENDER.render(settings, b"token-value", b"server-password", "a" * 32, self.ports())
        self.assertEqual(first, second)
        self.assertIn("Cluster_1/Caves/server.ini", first)
        self.assertIn(b"grass = \"often\"", first["Cluster_1/Master/worldgenoverride.lua"])
        self.assertEqual(first["Cluster_1/cluster_token.txt"], b"token-value")
        self.assertIn(b"cluster_password = server-password", first["Cluster_1/cluster.ini"])
        self.assertEqual(first["Cluster_1/adminlist.txt"], b"")

    def test_render_omits_disabled_caves_and_requires_complete_port_plan(self):
        settings = self.settings()
        settings["cavesEnabled"] = False
        rendered = RENDER.render(settings, b"token-value", b"", "a" * 32, self.ports()[:3])
        self.assertNotIn("Cluster_1/Caves/server.ini", rendered)
        with self.assertRaises(ValueError):
            RENDER.render(self.settings(), b"token-value", b"", "a" * 32, self.ports()[:3])

    def test_player_lists_reject_line_break_injection(self):
        with self.assertRaises(ValueError):
            RENDER.render_player_list(["safe-id\nmalicious-id"])

    def test_invalid_lua_values_and_keys_are_rejected(self):
        settings = self.settings()
        settings["worldgen"]["master"]["worldgenOverrides"]["bad-key"] = "often"
        with self.assertRaises(ValueError):
            RENDER.render_worldgen(settings, "master")
        settings = self.settings()
        settings["worldgen"]["master"]["worldgenOverrides"]["grass"] = '"; os.execute("bad")'
        with self.assertRaises(ValueError):
            RENDER.render_worldgen(settings, "master")


if __name__ == "__main__":
    unittest.main(verbosity=2)
