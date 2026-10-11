"""Actual grouped packages communicate using their public .sh/.exe entrypoints."""

import json
import os
import shutil
import subprocess
import unittest

from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent
from tests.test_launchers import LauncherFixture


@unittest.skipUnless(all(shutil.which(name) for name in ("snmpget", "snmpwalk", "snmpset")), "Net-SNMP clients required")
class PackagedIntegrationTests(LauncherFixture):
    integration_test = True

    def test_public_launchers_use_visible_profiles_for_real_get_set_capture_and_plot(self):
        platform = "Windows" if os.name == "nt" else "Linux"
        extension = "exe" if os.name == "nt" else "sh"
        agent_root = self.install_files("Agent", platform)
        manager_root = self.install_files("Manager", platform)
        launcher = manager_root / f"Hookah-Manager.{extension}"
        with RunningAgent(sample_interval=0.1, launcher=agent_root / f"Hookah-Agent.{extension}") as agent:
            profile = manager_root / "config.json"
            profile.write_text(json.dumps({"target": agent.target, "community": READ_COMMUNITY}), encoding="utf-8")

            def run(*arguments):
                result = subprocess.run([str(launcher), *map(str, arguments)], cwd=self.caller,
                                        env=self.environment(), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            self.assertIn("Python SNMP host agent", run("get", "--oid", ".1.3.6.1.2.1.1.1.0"))
            self.assertIn("STRING", run("walk", "--oid", ".1.3.6.1.2.1.2.2.1.2"))
            run("set", "--community", WRITE_COMMUNITY, "--oid", ".1.3.6.1.2.1.1.6.0", "--type", "s", "--value", "Grouped package smoke")
            self.assertIn("Grouped package smoke", run("get", "--oid", ".1.3.6.1.2.1.1.6.0"))
            capture = agent.path / "cpu.csv"
            run("capture", "--oid", ".1.3.6.1.2.1.25.3.3.1.2", "--duration", "0.35", "--sync-agent", "--output", capture)
            image = agent.path / "cpu.png"
            run("plot", capture, "--output", image, "--no-show")
            self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        for component, root in (("Agent", agent_root), ("Manager", manager_root)):
            self.assertEqual({path.name for path in root.iterdir() if not path.name.startswith(".")},
                             {f"Hookah-{component}.{extension}", "README.md", "HELP.md", "config.json"})
