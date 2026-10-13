"""Distribution completeness, portability, integrity, and verification gates."""

import contextlib
import copy
import io
import json
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

import build_release as release
import snmp_agent as agent
import snmp_manager_config as manager_config
from hookah_version import __version__
from release_tools.windows import ensure_windows_launchers, inspect_pe


def passing_reports():
    reports = {"version": __version__, "native_windows_execution": False}
    for suite in ("unit", "integration"):
        reports[suite] = {"suite": suite, "version": __version__, "status": "passed", "tests_run": 1,
                          "failures": [], "errors": [], "skipped": []}
    reports["smoke"] = {"suite": "smoke", "version": __version__, "status": "passed", "checks": ["fixture check"]}
    return reports


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-release-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.addCleanup(mock.patch.stopall)
        def small_bundle(component, platform, source=release.ROOT):
            folder = self.path / "runtime-fixtures" / platform / component
            interpreter = "python.exe" if platform == "Windows" else "bin/python"
            python = folder / "environment" / interpreter
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"isolated runtime test fixture")
            metadata = {"files": {interpreter: release.sha256(python)}, "executables": [interpreter]}
            (folder / "BUNDLE.json").write_text(json.dumps(metadata), encoding="utf-8")
            return folder
        mock.patch.object(release, "ensure_bundle", side_effect=small_bundle).start()
        mock.patch.object(release, "ensure_bundles", return_value={}).start()

    def test_agent_and_manager_contain_complete_independent_runtime_payloads(self):
        for component in ("Agent", "Manager"):
            for platform in release.PLATFORMS:
                launcher = release.launcher_name(component, platform)
                with self.subTest(component=component, platform=platform):
                    output = release.build_component(component, self.path / f"{component}-{platform}.zip", platform=platform)
                    manifest = release.verify_archive(output)
                    self.assertEqual(manifest["component"], component)
                    self.assertEqual(manifest["platform"], platform)
                    with zipfile.ZipFile(output) as archive:
                        names = set(archive.namelist())
                        self.assertEqual({name for name in names if "/" not in name}, {launcher, "README.md", "HELP.md", "config.json"})
                        for module in ("snmp_protocol.py", "snmp_mib.py", "snmp_cli_help.py", "snmp_crypto.py", "hookah_version.py"):
                            self.assertIn(".hookah/app/" + module, names)
                        entrypoint = "snmp_agent.py" if component == "Agent" else "snmp_data_collector_v12.py"
                        self.assertIn(".hookah/app/" + entrypoint, names)
                        self.assertIn(".hookah/launcher.py", names)
                        self.assertIn(".hookah/metadata/BUNDLE.json", names)
                        interpreter = "python.exe" if platform == "Windows" else "bin/python"
                        self.assertIn(".hookah/environment/" + interpreter, names)
                        self.assertIn(f".hookah/dependencies/requirements-{component.lower()}.txt", names)
                        self.assertEqual(archive.read(".hookah/metadata/VERSION").decode().strip(), __version__)
                        self.assertTrue(all(name.startswith(".hookah/") for name in names if name.endswith(".py")))
                        self.assertFalse(any(name.endswith((".pem", ".pyc", ".log")) or name.startswith((".venv", ".git/", ".idea")) for name in names))
                        document = json.loads(archive.read("config.json"))
                        if component == "Agent":
                            parsed = agent.config_from_document(document, self.path)
                            self.assertEqual(parsed.port, 1161)
                            self.assertTrue(parsed.allows_manager("127.0.0.1"))
                            self.assertFalse(parsed.allows_manager("192.168.1.10"))
                            self.assertIsNone(parsed.write_community)
                            self.assertFalse(parsed.encryption["enabled"])
                        else:
                            parsed = manager_config.config_from_document(document, self.path)
                            self.assertEqual(parsed.target, "udp:127.0.0.1:1161")
                            self.assertIsNone(parsed.snmp_bin)
                        if platform == "Windows":
                            self.assertEqual(inspect_pe(archive.read(launcher))["subsystem"], "console")

    def test_linux_executable_modes_and_windows_crlf_are_preserved(self):
        for component, platform in (("Agent", "Linux"), ("Manager", "Windows")):
            with self.subTest(component=component, platform=platform):
                output = release.build_component(component, self.path / f"{component}.zip", platform=platform)
                with zipfile.ZipFile(output) as archive:
                    for info in archive.infolist():
                        mode = info.external_attr >> 16
                        self.assertTrue(stat.S_ISDIR(mode) if info.is_dir() else stat.S_ISREG(mode))
                        executable = info.filename.endswith((".sh", ".exe")) or Path(info.filename).name in release.EXECUTABLE_FILES
                        expected = 0o755 if executable or info.is_dir() or info.filename == ".hookah/environment/bin/python" else 0o644
                        self.assertEqual(mode & 0o777, expected)
                        if ".hookah" in Path(info.filename).parts:
                            self.assertTrue(info.external_attr & 0x02)
                        if info.filename.endswith((".cmd", ".ps1")):
                            data = archive.read(info)
                            self.assertIn(b"\r\n", data)
                            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_component_builds_are_byte_for_byte_deterministic(self):
        first = release.build_component("Agent", self.path / "first.zip")
        second = release.build_component("Agent", self.path / "second.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_local_profiles_environments_and_private_keys_are_not_packaged(self):
        source = self.path / "source"
        for name in set(release.AGENT_FILES + release.MANAGER_FILES):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((release.ROOT / name).read_bytes())
        for path in (release.ROOT / "release_tools").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                destination = source / path.relative_to(release.ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
        for path in (release.ROOT / "build/windows").iterdir():
            if path.is_file():
                destination = source / "build/windows" / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
        for name in ("snmp_agent_config.json", "snmp_manager_config.json", "private.pem"):
            (source / name).write_text("developer-local-secret", encoding="utf-8")
        for component in ("Agent", "Manager"):
            with self.subTest(component=component):
                output = release.build_component(component, self.path / f"{component}.zip", source)
                with zipfile.ZipFile(output) as archive:
                    self.assertNotIn(b"developer-local-secret", archive.read("config.json"))
                    self.assertNotIn("private.pem", archive.namelist())

    def test_combined_release_includes_both_packages_source_tests_and_validation(self):
        outputs = release.build_release(self.path / "dist", passing_reports())
        for component in ("Agent", "Manager"):
            for platform in release.PLATFORMS:
                self.assertIn(f"Hookah-{component}-{platform}-{__version__}.zip", [output.name for output in outputs])
        self.assertEqual(outputs[-1].name, f"Hookah-{__version__}.zip")
        prefix = f"Hookah-{__version__}/"
        with zipfile.ZipFile(outputs[-1]) as archive:
            for component in ("Agent", "Manager"):
                for platform in release.PLATFORMS:
                    location = prefix + f"{component}/{platform}/"
                    self.assertIn(location + release.launcher_name(component, platform), archive.namelist())
                    self.assertIn(location + "config.json", archive.namelist())
            self.assertIn(prefix + ".hookah/source/tests/test_manager_cli.py", archive.namelist())
            self.assertIn(prefix + ".hookah/source/smoke_test.py", archive.namelist())
            self.assertIn(prefix + ".hookah/source/build_release.py", archive.namelist())
            self.assertEqual(json.loads(archive.read(prefix + ".hookah/metadata/VALIDATION.json")), passing_reports())
            self.assertEqual({name[len(prefix):] for name in archive.namelist() if "/" not in name[len(prefix):]}, {"README.md", "HELP.md"})
        checksums = (self.path / "dist" / "SHA256SUMS").read_text(encoding="utf-8")
        for output in outputs:
            release.verify_archive(output)
            self.assertIn(f"{release.sha256(output.read_bytes())}  {output.name}", checksums)

    def test_missing_inputs_do_not_overwrite_an_existing_output(self):
        output = self.path / "existing.zip"
        original = b"existing user artifact"
        output.write_bytes(original)
        with self.assertRaisesRegex(FileNotFoundError, "Missing package files"):
            release.build_component("Agent", output, self.path / "empty")
        self.assertEqual(output.read_bytes(), original)
        with self.assertRaises(ValueError):
            release.build_component("Unknown", output)

    def corrupted_copy(self, *, replacement=None, extra=None):
        original = release.build_component("Manager", self.path / "original.zip")
        with zipfile.ZipFile(original) as archive:
            entries = [(info, archive.read(info)) for info in archive.infolist()]
        corrupt = self.path / "corrupt.zip"
        with zipfile.ZipFile(corrupt, "w") as archive:
            for info, data in entries:
                archive.writestr(info, replacement[1] if replacement and info.filename == replacement[0] else data)
            if extra:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(extra[0], extra[1])
        return corrupt

    def test_tampered_payload_and_checksum_inventory_are_rejected(self):
        for filename, data, expected in ((".hookah/app/snmp_cli_help.py", b"tampered payload", "SHA-256"),
                                         (".hookah/metadata/SHA256SUMS", b"not checksums", "checksum inventory")):
            with self.subTest(filename=filename):
                corrupt = self.corrupted_copy(replacement=(filename, data))
                with self.assertRaisesRegex(ValueError, expected):
                    release.verify_archive(corrupt)

    def test_unsafe_duplicate_and_unlisted_members_are_rejected(self):
        for filename, expected in (("../escape.txt", "Unsafe ZIP"), ("C:/escape.txt", "Unsafe ZIP"),
                                   (".hookah/app/snmp_cli_help.py", "duplicate"), ("unexpected.txt", "manifest inventory")):
            with self.subTest(filename=filename):
                corrupt = self.corrupted_copy(extra=(filename, b"extra content"))
                with self.assertRaisesRegex(ValueError, expected):
                    release.verify_archive(corrupt)

    def test_public_package_layout_rejects_exposed_python_files(self):
        entries = release.component_entries("Manager", platform="Linux")
        entries["implementation.py"] = b"print('exposed')"
        with self.assertRaisesRegex(ValueError, "expose only"):
            release.write_archive(self.path / "invalid.zip", entries, "Manager", platform="Linux")
        self.assertFalse((self.path / "invalid.zip").exists())

    def test_renamed_scripts_and_non_console_binaries_cannot_replace_windows_executables(self):
        with self.assertRaisesRegex(ValueError, "real PE"):
            inspect_pe(b"@echo off\npython application.py\n")
        binary = bytearray(ensure_windows_launchers()["Manager"].read_bytes())
        import struct
        offset = struct.unpack_from("<I", binary, 0x3C)[0]
        struct.pack_into("<H", binary, offset + 24 + 68, 2)
        with self.assertRaisesRegex(ValueError, "console"):
            inspect_pe(binary)

    def test_crc_corruption_is_detected(self):
        output = self.path / "crc.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("payload.txt", b"data")
        with zipfile.ZipFile(output) as archive:
            info = archive.getinfo("payload.txt")
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(output.read_bytes())
        data[offset] ^= 1
        output.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "CRC"):
            release.verify_archive(output)

    def test_release_requires_successful_nonempty_matching_verification_reports(self):
        cases = [None, {}, {"version": "0.9.0"}]
        for suite in ("unit", "integration", "smoke"):
            failed = passing_reports()
            failed[suite]["status"] = "failed"
            cases.append(failed)
            empty = passing_reports()
            empty[suite]["checks" if suite == "smoke" else "tests_run"] = [] if suite == "smoke" else 0
            cases.append(empty)
        mismatch = passing_reports()
        mismatch["unit"]["version"] = "0.9.0"
        cases.append(mismatch)
        for validation in cases:
            with self.subTest(validation=validation), self.assertRaises(ValueError):
                release.build_release(self.path / "unverified", validation)
        self.assertFalse((self.path / "unverified").exists())

    def test_release_cli_runs_verification_before_packaging_and_stops_on_failure(self):
        events = []
        def verified(**kwargs):
            events.append("verification")
            return passing_reports()
        def packaged(*args):
            events.append("packaging")
            return []
        with mock.patch.object(release, "run_verification", side_effect=verified), \
                mock.patch.object(release, "build_release", side_effect=packaged), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(release.main(["--output-directory", str(self.path / "dist")]), 0)
        self.assertEqual(events, ["verification", "packaging"])
        with mock.patch.object(release, "run_verification", side_effect=subprocess.CalledProcessError(1, ["tests"])), \
                mock.patch.object(release, "build_release") as build, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(release.main([]), 1)
        build.assert_not_called()

    def test_verification_executes_all_three_suites_and_collects_their_reports(self):
        calls = []
        def execute(arguments, **kwargs):
            suite = "smoke" if "smoke_test.py" in arguments[2] else arguments[arguments.index("--suite") + 1]
            calls.append(suite)
            report = copy.deepcopy(passing_reports()[suite])
            Path(arguments[arguments.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(kwargs["check"])
        with mock.patch.object(release.subprocess, "run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
            reports = release.run_verification()
        self.assertEqual(calls, ["unit", "integration", "smoke"])
        release.validate_test_reports(reports)
