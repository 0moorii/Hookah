"""Real launchers in isolated directories and PowerShell setup verification."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import build_release as release
from hookah_version import __version__


class LauncherFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-launcher-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.caller = self.path / "Caller Space"
        self.caller.mkdir()

    def install_files(self, component):
        root = self.path / f"{component} With Spaces"
        for name, data in release.component_entries(component).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if os.name != "nt" and Path(name).name in release.EXECUTABLE_FILES:
                target.chmod(0o755)
        return root

    def environment(self):
        return dict(os.environ, HOOKAH_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1")


@unittest.skipIf(os.name == "nt", "Unix launchers require a Linux/POSIX shell")
class LinuxLauncherTests(LauncherFixture):
    def test_both_launchers_work_from_another_directory_with_spaces(self):
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_files(component)
                result = subprocess.run([str(root / f"hookah-{component.lower()}"), "--version"], cwd=self.caller,
                                        env=self.environment(), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"Hookah {component} {__version__}")

    def test_agent_loads_its_adjacent_configuration_and_cli_override_wins(self):
        root = self.install_files("Agent")
        launcher = root / "hookah-agent"
        result = subprocess.run([str(launcher), "config", "show"], cwd=self.caller, env=self.environment(),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout)["port"], 1161)
        custom = self.caller / "custom.json"
        custom.write_text('{"port": 22161}', encoding="utf-8")
        result = subprocess.run([str(launcher), "config", "show", "--config", str(custom)], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["port"], 22161)

    def test_manager_profile_precedence_and_adjacent_venv_selection(self):
        import json
        root = self.install_files("Manager")
        local = self.caller / "snmp_manager_config.json"
        local.write_text('{"target": "caller-device"}', encoding="utf-8")
        result = subprocess.run([str(root / "hookah-manager"), "config", "show"], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["target"], "caller-device")
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)
        environment = self.environment()
        environment.pop("HOOKAH_PYTHON")
        result = subprocess.run([str(root / "hookah-manager"), "--version"], cwd=self.caller,
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(__version__, result.stdout)

    def test_launchers_preserve_usage_error_and_missing_interpreter_exit_codes(self):
        root = self.install_files("Manager")
        result = subprocess.run([str(root / "hookah-manager"), "get", "--no-config"], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        environment = self.environment()
        environment["HOOKAH_PYTHON"] = str(self.path / "missing python")
        result = subprocess.run([str(root / "hookah-manager"), "--version"], cwd=self.caller,
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 127)


@unittest.skipUnless(os.name == "nt", "Native .cmd execution requires Windows")
class WindowsLauncherTests(LauncherFixture):
    def test_windows_cmd_launchers_quote_paths_and_report_versions(self):
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_files(component)
                result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                                         str(root / f"Hookah-{component}.cmd"), "--version"], cwd=self.caller,
                                        env=self.environment(), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"Hookah {component} {__version__}")

    def test_windows_cmd_launcher_propagates_application_usage_errors(self):
        root = self.install_files("Manager")
        result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                                 str(root / "Hookah-Manager.cmd"), "get", "--no-config"], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell required for script syntax/helper checks")
class PowerShellTests(unittest.TestCase):
    def run_script(self, script):
        environment = dict(os.environ, HOOKAH_TEST_ROOT=str(release.ROOT), HOOKAH_TEST_PYTHON=sys.executable)
        return subprocess.run([shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", script],
                              env=environment, capture_output=True, text=True, timeout=30)

    def test_every_powershell_setup_install_and_uninstall_script_parses(self):
        result = self.run_script(
            "$failed = $false; Get-ChildItem -LiteralPath (Join-Path $env:HOOKAH_TEST_ROOT 'windows') -Filter '*.ps1' | ForEach-Object { "
            "$tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; $failed = $true } }; if ($failed) { exit 1 }"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_setup_helpers_resolve_explicit_python_and_propagate_process_failures(self):
        result = self.run_script(
            ". (Join-Path $env:HOOKAH_TEST_ROOT 'windows/setup-common.ps1'); "
            "$command = Get-HookahPython $env:HOOKAH_TEST_PYTHON; "
            "Invoke-HookahChecked $command.Executable @('-B', '-c', 'print(12345)'); "
            "$caught = $false; try { Invoke-HookahChecked $command.Executable @('-B', '-c', 'raise SystemExit(7)') } "
            "catch { if ($_.Exception.Message -match 'Command failed \\(7\\)') { $caught = $true } }; "
            "if (-not $caught) { throw 'Setup helper failed to propagate the nonzero exit' }"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("12345", result.stdout)
