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
from release_tools.windows import find_compiler


WINDOWS_RUNNER = os.environ.get("HOOKAH_WINE") or shutil.which("wine") or shutil.which("wine64")


class LauncherFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-launcher-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.caller = self.path / "Caller Space"
        self.caller.mkdir()

    def install_files(self, component, platform="Linux"):
        root = self.path / f"{component} With Spaces"
        for name, data in release.component_entries(component, platform=platform, bundle_runtime=False).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if os.name != "nt" and (name.endswith((".sh", ".exe")) or Path(name).name in release.EXECUTABLE_FILES):
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
                result = subprocess.run([str(root / release.launcher_name(component, "Linux")), "--version"], cwd=self.caller,
                                        env=self.environment(), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"Hookah {component} {__version__}")

    def test_agent_loads_its_adjacent_configuration_and_cli_override_wins(self):
        root = self.install_files("Agent")
        launcher = root / "hookah-ag"
        result = subprocess.run([str(launcher), "--config-show"], cwd=self.caller, env=self.environment(),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout)["port"], 1161)
        custom = self.caller / "custom.json"
        custom.write_text('{"port": 22161}', encoding="utf-8")
        result = subprocess.run([str(launcher), "--config-show", "--config", str(custom)], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["port"], 22161)

    def test_manager_uses_visible_adjacent_profile_and_private_environment(self):
        import json
        root = self.install_files("Manager")
        local = self.caller / "snmp_manager_config.json"
        local.write_text('{"target": "caller-device"}', encoding="utf-8")
        (root / "config.json").write_text('{"target": "package-device"}', encoding="utf-8")
        result = subprocess.run([str(root / "hookah-mg"), "--config-show"], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["target"], "package-device")
        python = root / ".hookah" / "environment" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)
        environment = self.environment()
        environment.pop("HOOKAH_PYTHON")
        result = subprocess.run([str(root / "hookah-mg"), "--version"], cwd=self.caller,
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(__version__, result.stdout)

    def test_launchers_preserve_usage_error_and_missing_interpreter_exit_codes(self):
        root = self.install_files("Manager")
        result = subprocess.run([str(root / "hookah-mg"), "--get", "--no-config"], cwd=self.caller,
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        environment = self.environment()
        environment["HOOKAH_PYTHON"] = str(self.path / "missing python")
        result = subprocess.run([str(root / "hookah-mg"), "--version"], cwd=self.caller,
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 127)

    def test_user_help_names_the_public_launcher_and_creates_no_python_clutter(self):
        root = self.install_files("Manager")
        for arguments in (["--help"], []):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(root / "hookah-mg"), *arguments], cwd=self.caller,
                                        env=self.environment(), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("hookah-mg", result.stdout)
                self.assertNotIn("snmp_data_collector_v12.py", result.stdout)
        self.assertEqual({path.name for path in root.iterdir() if not path.name.startswith(".")},
                          {"hookah-mg", "README.md", "HELP.md", "config.json"})


@unittest.skipUnless(os.name == "nt" or WINDOWS_RUNNER, "Windows .exe execution requires Windows or Wine")
class WindowsLauncherTests(LauncherFixture):
    def run_windows(self, executable, *arguments, python=None):
        environment = dict(os.environ, WINEDEBUG="-all")
        environment.pop("HOOKAH_PYTHON", None)
        if python is not None:
            environment["HOOKAH_PYTHON"] = str(python) if os.name == "nt" else "Z:" + str(python).replace("/", "\\")
        command = [str(executable), *arguments]
        if os.name != "nt":
            command.insert(0, WINDOWS_RUNNER)
        return subprocess.run(command, cwd=self.caller, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_real_windows_executables_report_versions_and_help_without_python(self):
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_files(component, "Windows")
                result = self.run_windows(root / f"Hookah-{component}.exe", "--version")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"Hookah {component} {__version__}")
                result = self.run_windows(root / f"Hookah-{component}.exe", "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"Hookah-{component}.exe", result.stdout)
                self.assertNotIn(".py", result.stdout)

    def test_windows_launchers_forward_unicode_quotes_empty_values_and_exit_status(self):
        import json
        compiler = find_compiler()
        probe = self.path / "Probe With Spaces.exe"
        subprocess.run([compiler, "-municode", "-static", "-s", str(release.ROOT / "tests/fixtures/windows_argv_probe.c"),
                        "-o", str(probe)], check=True, timeout=60)
        arguments = ["capture", "--value", 'quoted "value"', "", "C:\\path with spaces\\", "Café 测试", "%PATH% & |", "--probe-exit", "7"]
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_files(component, "Windows")
                result = self.run_windows(root / f"Hookah-{component}.exe", *arguments, python=probe)
                self.assertEqual(result.returncode, 7, result.stderr)
                recorded = json.loads(result.stdout)
                self.assertEqual(recorded["argv"][:4], ["-I", "-X", "utf8", "-B"])
                self.assertTrue(recorded["argv"][4].endswith("\\.hookah\\launcher.py"))
                self.assertEqual(recorded["argv"][5:], arguments)
                self.assertEqual(recorded["program"], f"Hookah-{component}.exe")

    def test_missing_windows_interpreter_and_incomplete_package_fail_clearly(self):
        root = self.install_files("Manager", "Windows")
        result = self.run_windows(root / "Hookah-Manager.exe", "get", python=self.path / "missing interpreter.exe")
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("HOOKAH_PYTHON", result.stderr)
        (root / ".hookah" / "launcher.py").unlink()
        result = self.run_windows(root / "Hookah-Manager.exe", "get")
        self.assertEqual(result.returncode, 1)
        self.assertIn("support folder is missing", result.stderr)

    @unittest.skipUnless(os.name == "nt" or os.environ.get("HOOKAH_WINDOWS_TEST_PYTHON"),
                         "A real Windows Python runtime is required for application-level .exe tests")
    def test_windows_executables_launch_real_python_and_use_visible_profiles(self):
        import json
        selected = os.environ.get("HOOKAH_WINDOWS_TEST_PYTHON", sys.executable)
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_files(component, "Windows")
                document = {"port": 22161} if component == "Agent" else {"target": "visible-profile-device", "community": "Café profile"}
                (root / "config.json").write_text(json.dumps(document), encoding="utf-8-sig")
                python = Path(selected)
                result = self.run_windows(root / f"Hookah-{component}.exe", "config", "show", python=python)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                profile = json.loads(result.stdout)
                self.assertEqual(profile["port"] if component == "Agent" else profile["target"],
                                 22161 if component == "Agent" else "visible-profile-device")
                result = self.run_windows(root / f"Hookah-{component}.exe", "help", "config", "show", python=python)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"Hookah-{component}.exe", result.stdout)
                self.assertNotIn("snmp_agent.py", result.stdout)
                self.assertNotIn("snmp_data_collector_v12.py", result.stdout)


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
