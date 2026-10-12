"""Bundled runtime extraction, wheel installation, cache integrity, and offline startup."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import build_release as release
from release_tools import bundles
from tests.test_launchers import LauncherFixture, WINDOWS_RUNNER
from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent


class BundleBuilderUnitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-bundle-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def test_runtime_archive_is_extracted_with_materialized_safe_aliases(self):
        archive = self.path / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            executable = tarfile.TarInfo("python/bin/python3.12")
            executable.mode, executable.size = 0o755, 3
            package.addfile(executable, io.BytesIO(b"exe"))
            link = tarfile.TarInfo("python/bin/python")
            link.type, link.linkname = tarfile.SYMTYPE, "python3.12"
            package.addfile(link)
        bundles.extract_runtime(archive, self.path / "runtime")
        self.assertEqual((self.path / "runtime/bin/python").read_bytes(), b"exe")
        self.assertFalse((self.path / "runtime/bin/python").is_symlink())

    def test_unsafe_runtime_paths_and_links_are_rejected(self):
        for name, target in (("../escape", None), ("python/bin/python", "../../../escape")):
            with self.subTest(name=name):
                archive = self.path / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as package:
                    entry = tarfile.TarInfo(name)
                    if target:
                        entry.type, entry.linkname = tarfile.SYMTYPE, target
                    package.addfile(entry)
                with self.assertRaises(ValueError):
                    bundles.extract_runtime(archive, self.path / "bad-runtime")

    def test_wheel_contents_and_distribution_licenses_are_installed(self):
        wheel = self.path / "fixture.whl"
        with zipfile.ZipFile(wheel, "w") as package:
            package.writestr("fixture/__init__.py", b"READY = True\n")
            package.writestr("fixture-1.0.dist-info/METADATA", "Name: fixture\nVersion: 1.0\n")
            package.writestr("fixture-1.0.dist-info/licenses/LICENSE.txt", b"fixture license")
            package.writestr("fixture-1.0.data/platlib/native.pyd", b"native")
        versions = bundles.install_wheels([wheel], self.path / "environment", "Windows")
        site = self.path / "environment/Lib/site-packages"
        self.assertEqual(versions, {"fixture": "1.0"})
        self.assertTrue((site / "fixture/__init__.py").is_file())
        self.assertTrue((site / "fixture-1.0.dist-info/licenses/LICENSE.txt").is_file())
        self.assertEqual((site / "native.pyd").read_bytes(), b"native")

    def test_wheel_path_traversal_is_rejected(self):
        wheel = self.path / "bad.whl"
        with zipfile.ZipFile(wheel, "w") as package:
            package.writestr("../escape.py", b"bad")
        with self.assertRaises(ValueError):
            bundles.install_wheels([wheel], self.path / "environment", "Linux")

    def test_cache_detects_modified_runtime_files(self):
        environment = self.path / "environment"
        environment.mkdir()
        executable = environment / "python.exe"
        executable.write_bytes(b"fixture executable")
        fingerprint = bundles.bundle_fingerprint("Agent", "Windows")
        metadata = {"fingerprint": fingerprint, "files": {"python.exe": bundles.file_digest(executable)}}
        (self.path / "BUNDLE.json").write_text(json.dumps(metadata), encoding="utf-8")
        bundles.verify_bundle(self.path, "Agent", "Windows")
        executable.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "integrity"):
            bundles.verify_bundle(self.path, "Agent", "Windows")


class OfflineBundleTests(LauncherFixture):
    integration_test = True

    def install_bundle(self, component, platform):
        root = self.install_files(component, platform)
        folder = bundles.ensure_bundle(component, platform)
        shutil.copytree(folder / "environment", root / ".hookah/environment", copy_function=shutil.copy2)
        shutil.copy2(folder / "BUNDLE.json", root / ".hookah/metadata/BUNDLE.json")
        return root

    def offline_environment(self):
        if os.name != "nt" and not (self.caller / "dirname").exists():
            (self.caller / "dirname").symlink_to(shutil.which("dirname"))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1", WINEDEBUG="-all")
        for name in ("HOOKAH_PYTHON", "PYTHONPATH", "PYTHONHOME", "HOOKAH_MANAGER_CONFIG", "HOOKAH_SNMP_BACKEND"):
            environment.pop(name, None)
        environment["PATH"] = str(self.caller)
        return environment

    @unittest.skipIf(os.name == "nt", "Linux bundled interpreter test")
    def test_linux_agent_and_manager_run_without_system_python_or_pip(self):
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_bundle(component, "Linux")
                environment = self.offline_environment()
                blocker = self.caller / "python3"
                blocker.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
                blocker.chmod(0o755)
                launcher = root / f"Hookah-{component}.sh"
                command = ["config", "show"] if component == "Agent" else ["doctor"]
                for arguments in (command, ["setup"]):
                    result = subprocess.run([str(launcher), *arguments], env=environment, cwd=self.caller,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("Collecting", result.stdout)
                if component == "Manager":
                    self.assertIn("bundled PySNMP", result.stdout)
                self.assertEqual({path.name for path in root.iterdir() if not path.name.startswith(".")},
                                 {launcher.name, "README.md", "HELP.md", "config.json"})

    @unittest.skipIf(os.name == "nt", "Linux offline communication smoke check")
    def test_linux_bundled_agent_manager_and_plot_work_offline(self):
        agent_root = self.install_bundle("Agent", "Linux")
        manager_root = self.install_bundle("Manager", "Linux")
        (self.caller / "dirname").symlink_to(shutil.which("dirname"))
        environment = self.offline_environment()
        with RunningAgent(sample_interval=0.1, launcher=agent_root / "Hookah-Agent.sh", environment=environment) as agent:
            def manager(*arguments):
                result = subprocess.run([str(manager_root / "Hookah-Manager.sh"), *map(str, arguments)],
                                        env=environment, cwd=self.caller, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            connection = ["--target", agent.target, "--community", READ_COMMUNITY]
            for version in ("1", "2c"):
                with self.subTest(version=version):
                    self.assertIn("Python SNMP host agent", manager("get", *connection, "--version", version, "--oid", ".1.3.6.1.2.1.1.1.0"))
                    self.assertIn("STRING", manager("walk", *connection, "--version", version, "--oid", ".1.3.6.1.2.1.2.2.1.2"))
            manager("set", "--target", agent.target, "--community", WRITE_COMMUNITY, "--oid", ".1.3.6.1.2.1.1.6.0", "--type", "s", "--value", "Offline Café")
            self.assertIn("Offline Café", manager("get", *connection, "--oid", ".1.3.6.1.2.1.1.6.0"))
            csv = agent.path / "cpu.csv"
            manager("capture", *connection, "--oid", ".1.3.6.1.2.1.25.3.3.1.2", "--duration", "0.35", "--sync-agent", "--output", csv)
            png = agent.path / "cpu.png"
            manager("plot", csv, "--output", png, "--no-show")
            self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            manager("keys", "generate", "--directory", agent.path / "keys", "--bits", "2048")

    @unittest.skipUnless(os.name == "nt" or WINDOWS_RUNNER, "Windows or Wine is required")
    def test_windows_agent_and_manager_run_without_system_python_or_pip(self):
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                root = self.install_bundle(component, "Windows")
                launcher = root / f"Hookah-{component}.exe"
                prefix = [str(launcher)] if os.name == "nt" else [WINDOWS_RUNNER, str(launcher)]
                environment = self.offline_environment()
                for arguments in (["config", "show"] if component == "Agent" else ["doctor"], ["setup"]):
                    result = subprocess.run([*prefix, *arguments], env=environment, cwd=self.caller,
                                            capture_output=True, text=True, encoding="utf-8", timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("Collecting", result.stdout)
                if component == "Manager":
                    self.assertIn("bundled PySNMP", result.stdout)

    @unittest.skipUnless(os.name == "nt" or WINDOWS_RUNNER, "Windows or Wine is required")
    def test_windows_bundled_manager_performs_snmp_plotting_and_key_generation(self):
        root = self.install_bundle("Manager", "Windows")
        executable = root / "Hookah-Manager.exe"
        command = [str(executable)] if os.name == "nt" else [WINDOWS_RUNNER, str(executable)]
        environment = self.offline_environment()
        def winpath(path):
            return str(path) if os.name == "nt" else "Z:" + str(path).replace("/", "\\")
        with RunningAgent(sample_interval=0.1) as agent:
            connection = ["--target", agent.target, "--community", READ_COMMUNITY]
            def run(*arguments):
                result = subprocess.run([*command, *arguments], env=environment, cwd=self.caller,
                                        capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            for version in ("1", "2c"):
                self.assertIn("Python SNMP host agent", run("get", *connection, "--version", version, "--oid", ".1.3.6.1.2.1.1.1.0"))
                self.assertIn("STRING", run("walk", *connection, "--version", version, "--oid", ".1.3.6.1.2.1.2.2.1.2"))
            run("set", "--target", agent.target, "--community", WRITE_COMMUNITY, "--oid", ".1.3.6.1.2.1.1.6.0", "--type", "s", "--value", "Bundled Windows Café")
            self.assertIn("Bundled Windows Café", run("get", *connection, "--oid", ".1.3.6.1.2.1.1.6.0"))
            csv, png = agent.path / "windows-cpu.csv", agent.path / "windows-cpu.png"
            run("capture", *connection, "--oid", ".1.3.6.1.2.1.25.3.3.1.2", "--interval", "0.1", "--duration", "0.4", "--output", winpath(csv))
            run("plot", winpath(csv), "--output", winpath(png), "--no-show")
            self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            run("keys", "generate", "--directory", winpath(agent.path / "windows-keys"), "--bits", "2048")

    @unittest.skipUnless(os.name == "nt" or WINDOWS_RUNNER, "Windows or Wine is required")
    def test_windows_bundled_agent_collects_metrics_without_dependency_setup(self):
        root = self.install_bundle("Agent", "Windows")
        executable = root / "Hookah-Agent.exe"
        command = [str(executable)] if os.name == "nt" else [WINDOWS_RUNNER, str(executable)]
        result = subprocess.run([*command, "--list-oids"], env=self.offline_environment(), cwd=self.caller,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Python SNMP host agent", result.stdout)
        self.assertIn(".1.3.6.1.2.1.25.2.2.0", result.stdout)
