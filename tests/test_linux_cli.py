"""Linux-only parameter commands, saved Agents, PATH installation, and real traffic."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import build_release as release
from hookah_version import __version__
from release_tools import linux_cli
from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent
from tests.test_launchers import LauncherFixture


class LinuxParameterFixture(LauncherFixture):
    def invoke(self, root, *arguments, expected=0, environment=None):
        component = json.loads((root / ".hookah/metadata/component.json").read_text())["component"]
        result = subprocess.run([str(root / release.launcher_name(component, "Linux")), *map(str, arguments)],
                                cwd=self.caller, env=environment or self.environment(), capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def profile(self, root):
        return json.loads((root / "config.json").read_text(encoding="utf-8-sig"))


@unittest.skipIf(os.name == "nt", "Linux package entrypoints require a POSIX shell")
class LinuxParameterTests(LinuxParameterFixture):
    def test_both_linux_commands_support_short_version_and_parameter_help(self):
        for component in ("Agent", "Manager"):
            root = self.install_files(component)
            with self.subTest(component=component):
                self.assertEqual(self.invoke(root, "-v").stdout.strip(), f"Hookah {component} {__version__}")
                help_text = self.invoke(root, "--help").stdout
                self.assertIn(release.launcher_name(component, "Linux"), help_text)
                self.assertIn("--config-set", help_text)
                self.assertIn("--mib", help_text)
                self.assertNotIn("snmp_data_collector_v12.py", help_text)
                self.assertNotIn("snmp_agent.py", help_text)

    def test_manager_without_parameters_prints_help_on_a_real_terminal(self):
        root = self.install_files("Manager")
        master, terminal = os.openpty()
        try:
            result = subprocess.run([str(root / "hookah-mg")], stdin=terminal, cwd=self.caller,
                                    env=self.environment(), capture_output=True, text=True, timeout=15)
        finally:
            os.close(master)
            os.close(terminal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: hookah-mg", result.stdout)
        self.assertNotIn("Choose an option", result.stdout)
        self.assertNotIn("SNMP DATA COLLECTOR", result.stdout)

    def test_numbered_and_interactive_commands_are_rejected(self):
        root = self.install_files("Manager")
        for argument in ("1", "0", "interactive", "--interactive", "get"):
            with self.subTest(argument=argument):
                self.invoke(root, argument, expected=2)

    def test_saved_agents_add_select_remove_and_deduplicate(self):
        root = self.install_files("Manager")
        self.invoke(root, "-a", "192.168.1.10")
        self.assertEqual(self.profile(root)["target"], "udp:192.168.1.10:1161")
        self.invoke(root, "-a", "192.168.1.11", "--port", "161")
        self.invoke(root, "-a", "udp:192.168.1.10:1161")
        agents = json.loads((root / ".hookah/config/agents.json").read_text())["agents"]
        self.assertEqual(agents, ["udp:192.168.1.10:1161", "udp:192.168.1.11:161"])
        self.assertIn("* udp:192.168.1.10:1161", self.invoke(root, "--agents").stdout)
        self.invoke(root, "--select-agent", "192.168.1.11")
        self.assertEqual(self.profile(root)["target"], "udp:192.168.1.11:161")
        self.invoke(root, "--remove-agent", "192.168.1.11")
        self.assertEqual(self.profile(root)["target"], "udp:192.168.1.10:1161")
        self.invoke(root, "--remove-agent", "192.168.1.10")
        self.assertIsNone(self.profile(root)["target"])
        self.assertIn("No agents saved", self.invoke(root, "--agents").stdout)
        self.invoke(root, "--get", "--oid", ".1.3.6.1.2.1.1.1.0", expected=2)
        self.assertEqual({path.name for path in root.iterdir() if not path.name.startswith(".")},
                         {"hookah-mg", "README.md", "HELP.md", "config.json"})

    def test_invalid_addresses_do_not_change_the_profile_or_saved_list(self):
        root = self.install_files("Manager")
        profile = (root / "config.json").read_bytes()
        registry = (root / ".hookah/config/agents.json").read_bytes()
        for address in ("999.168.1.1", "bad address", "bad..hostname", "127.0.0.1:0", "127.0.0.1:70000", "::1"):
            with self.subTest(address=address):
                self.invoke(root, "-a", "192.168.1.10", "-a", address, expected=1)
                self.assertEqual((root / "config.json").read_bytes(), profile)
                self.assertEqual((root / ".hookah/config/agents.json").read_bytes(), registry)
        self.invoke(root, "--remove-agent", "192.168.1.99", expected=1)
        self.assertEqual((root / "config.json").read_bytes(), profile)

    def test_multiple_ports_require_an_explicit_selection(self):
        root = self.install_files("Manager")
        self.invoke(root, "-a", "192.168.1.10:1161", "-a", "192.168.1.10:161")
        self.invoke(root, "--select-agent", "192.168.1.10", expected=1)
        self.invoke(root, "--select-agent", "192.168.1.10:161")
        self.assertEqual(self.profile(root)["target"], "udp:192.168.1.10:161")

    def test_custom_profiles_have_separate_saved_lists(self):
        root = self.install_files("Manager")
        original = (root / "config.json").read_bytes()
        custom = self.caller / "another profile.json"
        custom.write_text('{"target": "udp:127.0.0.1:1161", "community": "custom"}', encoding="utf-8")
        self.invoke(root, "-a", "192.168.1.30", "--config", custom)
        self.assertEqual((root / "config.json").read_bytes(), original)
        self.assertIn("No agents saved", self.invoke(root, "--agents").stdout)
        self.assertIn("192.168.1.30", self.invoke(root, "--agents", "--config", custom).stdout)
        self.assertEqual(json.loads(custom.read_text())["community"], "custom")
        self.assertEqual(len(list((root / ".hookah/config").glob("agents-*.json"))), 1)

    def test_registry_failure_restores_the_original_profile_bytes_and_permissions(self):
        root = self.install_files("Manager")
        profile = root / "config.json"
        profile.write_bytes(b'\xef\xbb\xbf{"target":"udp:127.0.0.1:1161"}\n')
        profile.chmod(0o640)
        original = profile.read_bytes()
        write = linux_cli.atomic_json
        def fail_registry(path, document):
            if Path(path).name == "agents.json":
                raise PermissionError("registry fixture failure")
            return write(path, document)
        args = linux_cli.build_parser("Manager", __version__).parse_args(["-a", "192.168.1.10"])
        with mock.patch.object(linux_cli, "atomic_json", side_effect=fail_registry), self.assertRaises(PermissionError):
            linux_cli.manage_agents(root, profile, "add_agent", args)
        self.assertEqual(profile.read_bytes(), original)
        self.assertEqual(profile.stat().st_mode & 0o777, 0o640)

    def test_manager_environment_profile_and_explicit_override_are_preserved(self):
        root = self.install_files("Manager")
        custom = self.caller / "environment.json"
        custom.write_text('{"target":"environment-agent"}', encoding="utf-8")
        environment = dict(self.environment(), HOOKAH_MANAGER_CONFIG=str(custom))
        selected = self.invoke(root, "--config-show", environment=environment)
        self.assertEqual(json.loads(selected.stdout)["target"], "environment-agent")
        explicit = self.invoke(root, "--config-show", "--config", root / "config.json", environment=environment)
        self.assertEqual(json.loads(explicit.stdout)["target"], "udp:127.0.0.1:1161")

    def test_manager_profile_changes_validate_and_preserve_relative_paths(self):
        root = self.install_files("Manager")
        document = self.profile(root)
        document["snmp_bin"] = "tools"
        (root / "config.json").write_text(json.dumps(document), encoding="utf-8")
        self.invoke(root, "--config-set", "--community", "new community", "--snmp-version", "1", "--timeout", "0.5",
                    "--retries", "3", "--interval", "10", "--counter-bits", "64")
        profile = self.profile(root)
        self.assertEqual(profile["community"], "new community")
        self.assertEqual(profile["version"], "1")
        self.assertEqual(profile["timeout"], 0.5)
        self.assertEqual(profile["interval"], 10)
        self.assertEqual(profile["snmp_bin"], "tools")
        original = (root / "config.json").read_bytes()
        self.invoke(root, "--config-set", "--retries", "11", expected=2)
        self.invoke(root, "--config-set", expected=2)
        self.assertEqual((root / "config.json").read_bytes(), original)

    def test_encryption_profile_can_be_saved_and_disabled_without_a_menu(self):
        root = self.install_files("Manager")
        keys = self.caller / "keys with spaces"
        self.invoke(root, "--generate-keys", "--directory", keys, "--bits", "2048")
        profile = keys / "manager_encryption.json"
        self.invoke(root, "--config-set", "--encryption-config", profile)
        self.assertEqual(self.profile(root)["encryption_config"], str(profile))
        self.invoke(root, "--config-set", "--disable-encryption")
        self.assertIsNone(self.profile(root)["encryption_config"])
        original = (root / "config.json").read_bytes()
        self.invoke(root, "--config-set", "--encryption-config", keys / "missing.json", expected=1)
        self.assertEqual((root / "config.json").read_bytes(), original)

    def test_agent_addresses_append_to_the_existing_manager_allowlist(self):
        root = self.install_files("Agent")
        self.invoke(root, "-a", "192.168.1.10", "-a", "192.168.2.0/24")
        self.invoke(root, "-a", "192.168.1.10")
        self.assertEqual(self.profile(root)["allowed_managers"], ["127.0.0.1/32", "192.168.1.10/32", "192.168.2.0/24"])
        original = (root / "config.json").read_bytes()
        self.invoke(root, "-a", "not-an-ip", expected=1)
        self.assertEqual((root / "config.json").read_bytes(), original)

    def test_agent_collection_changes_use_parameters(self):
        root = self.install_files("Agent")
        self.invoke(root, "--config-set", "--sample-interval", "10", "--interval", "ifInOctets", "120",
                    "--set", "write_community", "linux-write")
        profile = self.profile(root)
        self.assertEqual(profile["sample_interval"], 10)
        self.assertEqual(profile["collection_intervals"]["ifInOctets"], 120)
        self.assertEqual(profile["write_community"], "linux-write")
        self.invoke(root, "--config-set", "--clear-interval", "ifInOctets")
        self.assertNotIn("ifInOctets", self.profile(root)["collection_intervals"])

    def test_missing_arguments_and_unrelated_parameters_are_usage_errors(self):
        root = self.install_files("Manager")
        cases = [("--get",), ("--set", "--oid", ".1.3.6.1.2.1.1.6.0"), ("--capture", "--oid", ".1.3.6.1.2.1.1.1.0"),
                 ("--get", "--oid", ".1.3.6.1.2.1.1.1.0", "--quiet"), ("--agents", "--no-config"),
                 ("--plot", "missing.csv", "--community", "public"), ("--get", "--walk")]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.invoke(root, *arguments, expected=2)
        agent = self.install_files("Agent")
        self.invoke(agent, "--start", "--remote", "192.168.1.10", expected=2)

    def test_every_original_manager_operation_has_a_parameter_translation(self):
        root = self.install_files("Manager")
        cases = [("get", ["--get", "--oid", ".1.3.6.1.2.1.1.1.0"]),
                 ("walk", ["--walk", "--oid", ".1.3.6.1"]),
                 ("set", ["--set", "--oid", ".1.3.6.1.2.1.1.6.0", "--type", "s", "--value", "room"]),
                 ("capture", ["--capture", "--oid", ".1.3.6.1.2.1.1.1.0", "--output", "capture.csv"]),
                 ("plot", ["--plot", "capture.csv"]), ("compare", ["--compare", "before.csv", "after.csv"]),
                 ("catalog", ["--catalog"]), ("mib", ["--mib"]), ("browse", ["--browse"]),
                 ("generate_keys", ["--generate-keys"])]
        for operation, arguments in cases:
            with self.subTest(operation=operation):
                parser = linux_cli.build_parser("Manager", __version__)
                args = parser.parse_args(arguments)
                translated = linux_cli.manager_arguments(parser, root, root / "config.json", operation, args)
                self.assertIn("keys" if operation == "generate_keys" else operation, translated)
                self.assertNotIn("interactive", translated)

    def test_installation_supports_both_names_spaces_and_symlink_resolution(self):
        directory = self.caller / "Bin With Spaces"
        for component in ("Agent", "Manager"):
            root = self.install_files(component)
            self.invoke(root, "--install-cli", "--bin-dir", directory)
            self.invoke(root, "--install-cli", "--bin-dir", directory)
            launcher = release.launcher_name(component, "Linux")
            self.assertEqual((directory / launcher).resolve(), root / launcher)
            environment = dict(self.environment(), PATH=str(directory) + os.pathsep + os.environ["PATH"])
            result = subprocess.run([launcher, "-v"], cwd=self.caller, env=environment,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"Hookah {component} {__version__}")

    def test_installation_never_overwrites_an_unrelated_command(self):
        root = self.install_files("Manager")
        directory = self.caller / "bin"
        directory.mkdir()
        existing = directory / "hookah-mg"
        existing.write_bytes(b"user command")
        self.invoke(root, "--install-cli", "--bin-dir", directory, expected=1)
        self.assertEqual(existing.read_bytes(), b"user command")

    def test_parameter_help_and_version_do_not_import_application_dependencies(self):
        root = self.install_files("Agent")
        for arguments in (["-v"], ["--help"]):
            with mock.patch.object(linux_cli.importlib, "import_module", side_effect=AssertionError("unexpected application import")), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                linux_cli.main(arguments, root)
            self.assertEqual(stopped.exception.code, 0)

    def test_windows_packages_keep_the_original_runtime_and_no_linux_adapter(self):
        for component in ("Agent", "Manager"):
            entries = release.component_entries(component, platform="Windows", bundle_runtime=False)
            self.assertIn(f"Hookah-{component}.exe", entries)
            self.assertNotIn("hookah-ag", entries)
            self.assertNotIn("hookah-mg", entries)
            self.assertNotIn(".hookah/app/hookah_runtime.py", entries)
            self.assertNotIn(".hookah/docs/LINUX-CLI.md", entries)
            self.assertEqual(entries[".hookah/launcher.py"], (release.ROOT / "release_tools/runtime.py").read_bytes())


@unittest.skipUnless(os.name != "nt" and shutil.which("snmpget"), "Linux loopback SNMP tools required")
class LinuxParameterIntegrationTests(LinuxParameterFixture):
    integration_test = True

    def test_saved_agent_queries_csv_only_live_mib_and_remote_configuration(self):
        root = self.install_files("Manager")
        agent_root = self.install_files("Agent")
        with RunningAgent(sample_interval=0.1, launcher=agent_root / "hookah-ag") as agent:
            self.invoke(root, "-a", agent.target)
            self.invoke(root, "--config-set", "--community", READ_COMMUNITY)
            output = self.invoke(root, "--get", "--oid", ".1.3.6.1.2.1.1.1.0").stdout
            self.assertIn("Python SNMP host agent", output)
            csv = self.caller / "values with spaces.csv"
            self.assertEqual(self.invoke(root, "--get", "--oid", ".1.3.6.1.2.1.1.1.0", "--output", csv, "--quiet").stdout, "")
            self.assertIn("Python SNMP host agent", csv.read_text())
            tree = self.invoke(root, "--mib", "--group", "memory", "--values").stdout
            self.assertIn("Displayed", tree)
            self.invoke(agent_root, "--config-set", "--remote", agent.target, "--community", WRITE_COMMUNITY,
                        "--snmp-version", "2c", "--interval", "cpu", "10")
            remote = self.invoke(agent_root, "--config-show", "--remote", agent.target, "--community", READ_COMMUNITY)
            self.assertEqual(json.loads(remote.stdout)["collection_intervals"]["cpu"], 10)
