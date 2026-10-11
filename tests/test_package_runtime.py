"""The hidden package runtime preserves visible profiles and setup isolation."""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from release_tools import runtime
from tests.test_launchers import LauncherFixture


class PackageRuntimeTests(LauncherFixture):
    def test_application_commands_keep_code_hidden_and_forward_config_overrides(self):
        root = self.install_files("Agent")
        command = runtime.app_command(root, "Agent", ["config", "show", "--config", "custom.json"])
        self.assertEqual(Path(command[4]), root / ".hookah/app/snmp_agent.py")
        self.assertEqual(command[5:7], ["--config", str(root / "config.json")])
        self.assertEqual(command[-2:], ["--config", "custom.json"])
        command = runtime.app_command(root, "Manager", ["config", "init"])
        self.assertEqual(command[-2:], ["--config", str(root / "config.json")])

    def test_version_and_help_work_without_optional_dependencies(self):
        root = self.install_files("Agent")
        with mock.patch.object(runtime.importlib.util, "find_spec", side_effect=AssertionError("unnecessary dependency lookup")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runtime.main(["--version"], root), 0)
            self.assertEqual(runtime.main(["--help"], root), 0)
        self.assertIn("Hookah Agent", output.getvalue())
        self.assertNotIn("snmp_agent.py", output.getvalue())

    def test_missing_agent_dependencies_give_public_setup_instruction(self):
        root = self.install_files("Agent")
        with mock.patch.object(runtime.importlib.util, "find_spec", return_value=None), \
                mock.patch.dict(os.environ, {"HOOKAH_PROGRAM_NAME": "Hookah-Agent.sh"}), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(runtime.main([], root), 1)
        self.assertIn("Hookah-Agent.sh setup", error.getvalue())
        self.assertNotIn("pip", error.getvalue())

    def test_setup_uses_only_private_environment_and_preserves_existing_config(self):
        root = self.install_files("Agent")
        config = root / "config.json"
        config.write_text('{"port": 22161}', encoding="utf-8")
        original = config.read_bytes()
        with mock.patch.object(runtime, "create_environment") as builder, \
                mock.patch.object(runtime.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.setup(root, "Agent", "Hookah-Agent.sh", []), 0)
        builder.assert_called_once_with(root / ".hookah/environment")
        self.assertEqual(config.read_bytes(), original)
        install = run.call_args_list[0].args[0]
        self.assertEqual(Path(install[0]), runtime.environment_python(root))
        self.assertIn(str(root / ".hookah/dependencies/requirements-agent.txt"), install)

    def test_setup_restores_missing_config_from_private_default(self):
        root = self.install_files("Agent")
        (root / "config.json").unlink()
        with mock.patch.object(runtime, "create_environment"), mock.patch.object(runtime.subprocess, "run"), \
                contextlib.redirect_stdout(io.StringIO()):
            runtime.setup(root, "Agent", "Hookah-Agent.sh", [])
        self.assertEqual(json.loads((root / "config.json").read_text())["port"], 1161)

    def test_repeated_setup_reuses_the_existing_environment(self):
        root = self.install_files("Agent")
        python = runtime.environment_python(root)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"installed interpreter")
        with mock.patch.object(runtime, "create_environment") as builder, mock.patch.object(runtime.subprocess, "run"), \
                contextlib.redirect_stdout(io.StringIO()):
            runtime.setup(root, "Agent", "Hookah-Agent.sh", [])
        builder.assert_not_called()

    def test_dependency_install_failure_is_reported_without_overwriting_profile(self):
        root = self.install_files("Manager")
        original = (root / "config.json").read_bytes()
        with mock.patch.object(runtime, "create_environment"), \
                mock.patch.object(runtime.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["pip"])), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(runtime.main(["setup"], root), 1)
        self.assertTrue(error.getvalue())
        self.assertEqual((root / "config.json").read_bytes(), original)

    def test_service_commands_reject_unsupported_component_or_operating_system(self):
        root = self.install_files("Manager")
        with self.assertRaises(ValueError):
            runtime.service(root, "Manager", ["install"])
        if os.name != "nt":
            with self.assertRaises(ValueError):
                runtime.service(root, "Agent", ["install"])

    def test_agent_accepts_utf8_bom_in_visible_windows_profile(self):
        root = self.install_files("Agent")
        (root / "config.json").write_text('{"port": 22161}', encoding="utf-8-sig")
        command = [str(root / "Hookah-Agent.sh"), "config", "show"] if os.name != "nt" else [sys.executable, str(root / ".hookah/launcher.py"), "config", "show"]
        environment = dict(self.environment(), HOOKAH_PACKAGE_ROOT=str(root))
        result = subprocess.run(command, cwd=self.caller, env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["port"], 22161)
