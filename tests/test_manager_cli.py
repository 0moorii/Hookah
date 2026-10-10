"""Manager CLI profiles, Linux automation behavior, commands, and headless plots."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snmp_agent as agent
import snmp_data_collector_v12 as manager
import snmp_manager_config as settings
from hookah_version import __version__


OID = ".1.3.6.1.2.1.1.5.0"
RECORD = (OID, "STRING", '"device"', None)


class ManagerCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-cli-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def call(self, arguments, *, parse_exit=None):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            if parse_exit is None:
                status = manager.main(arguments)
            else:
                with self.assertRaises(SystemExit) as stopped:
                    manager.main(arguments)
                status = stopped.exception.code
                self.assertEqual(status, parse_exit)
        return status, output.getvalue(), error.getvalue()

    def test_application_versions_and_protocol_version_are_distinct(self):
        status, output, _ = self.call(["--version"], parse_exit=0)
        self.assertEqual(output.strip(), f"Hookah Manager {__version__}")
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as stopped:
            agent.main(["--version"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"Hookah Agent {__version__}")
        parsed = manager.build_manager_parser().parse_args(["get", "--version", "1", "--oid", OID])
        self.assertEqual(parsed.version, "1")

    def test_no_arguments_with_redirected_input_print_help_without_prompting(self):
        with mock.patch.object(manager.sys, "stdin", io.StringIO("0\n")), \
                mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            status, output, error = self.call([])
        self.assertEqual(status, 0)
        self.assertIn("usage:", output)
        self.assertNotIn("Made by 0moorii", output)
        self.assertEqual(error, "")

    def test_terminal_without_arguments_opens_menu(self):
        with mock.patch.object(manager.sys, "stdin") as terminal, mock.patch.object(manager, "interactive_main") as menu:
            terminal.isatty.return_value = True
            status, output, _ = self.call([])
        self.assertEqual(status, 0)
        menu.assert_called_once()
        self.assertIn("Made by 0moorii", output)

    def test_offline_menu_options_work_without_net_snmp(self):
        with mock.patch.object(manager, "check_dependencies") as dependency_check, \
                mock.patch("builtins.input", side_effect=["6", "10", "0"]):
            status, output, _ = self.call(["interactive", "--no-banner"])
        self.assertEqual(status, 0)
        self.assertIn("COMMON SNMP OID REFERENCE", output)
        self.assertIn("hostResources", output)
        self.assertIn("Goodbye", output)
        self.assertNotIn("Made by 0moorii", output)
        dependency_check.assert_not_called()

    def test_eof_and_ctrl_c_exit_cleanly(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            status, output, _ = self.call(["interactive"])
        self.assertEqual(status, 0)
        self.assertIn("Goodbye", output)
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "snmp_get", side_effect=KeyboardInterrupt):
            status, _, error = self.call(["get", "--no-config", "--target", "device", "--oid", OID])
        self.assertEqual(status, 130)
        self.assertIn("Stopped by user", error)

    def test_broken_output_pipe_has_no_error_traceback(self):
        class ClosedPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("consumer closed")

        with contextlib.redirect_stdout(ClosedPipe()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(manager.main(["catalog"]), 0)
        self.assertEqual(error.getvalue(), "")

    def test_offline_help_catalog_and_tree_do_not_check_network_dependencies(self):
        for arguments in (["help"], ["help", "plot"], ["catalog"], ["mib", "--group", "memory"]):
            with self.subTest(arguments=arguments), mock.patch.object(manager, "check_dependencies") as check:
                status, output, _ = self.call(arguments)
                self.assertEqual(status, 0)
                self.assertTrue(output)
                self.assertNotIn("Made by 0moorii", output)
                check.assert_not_called()

    def test_profile_init_show_and_existing_file_protection(self):
        profile = self.path / "profiles" / "lab.json"
        status, output, _ = self.call(["config", "init", "--config", str(profile)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["target"], "udp:127.0.0.1:1161")
        original = profile.read_bytes()
        status, _, error = self.call(["config", "init", "--config", str(profile)])
        self.assertEqual(status, 1)
        self.assertTrue(error)
        self.assertEqual(profile.read_bytes(), original)
        status, output, _ = self.call(["--config", str(profile), "config", "show"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), json.loads(original))
        if os.name == "posix":
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)

    def test_cli_overrides_profile_and_restores_shared_session_settings(self):
        profile = self.path / "manager.json"
        profile.write_text(json.dumps({"target": "profile-device", "version": "2c", "community": "profile-read",
                                       "timeout": 20, "retries": 3}), encoding="utf-8")
        prior = manager._MANAGER_CONFIG, manager._TRAFFIC_CIPHER
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "run_command", return_value=f'{OID} = STRING: "device"') as command:
            status, output, error = self.call(["get", "--config", str(profile), "--target", "cli-device",
                                              "--version", "1", "--community", "cli-read", "--timeout", "4",
                                              "--retries", "2", "--oid", OID, "--output", str(self.path / "get")])
        self.assertEqual((status, error), (0, ""))
        arguments = command.call_args.args[0]
        self.assertEqual(arguments[0:5], ["snmpget", "-v", "1", "-c", "cli-read"])
        self.assertEqual(arguments[7:12], ["-t", "4", "-r", "2", "cli-device"])
        self.assertNotIn("Made by 0moorii", output)
        self.assertTrue((self.path / "get.csv").exists())
        self.assertEqual((manager._MANAGER_CONFIG, manager._TRAFFIC_CIPHER), prior)

    def test_explicit_banner_preserves_scriptable_opt_in(self):
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "snmp_get", return_value=RECORD):
            status, output, _ = self.call(["get", "--no-config", "--target", "device", "--oid", OID, "--banner"])
        self.assertEqual(status, 0)
        self.assertIn("Made by 0moorii", output)
        self.assertIn(OID, output)

    def test_missing_target_invalid_options_and_conflicting_config_flags_are_usage_errors(self):
        cases = (
            ["get", "--no-config", "--oid", OID],
            ["get", "--oid", "--not-an-oid"],
            ["get", "--oid", "sysName.0"],
            ["capture", "--oid", OID, "--output", "unused", "--interval", "nan"],
            ["get", "--oid", OID, "--retries", "11"],
            ["--config", "anything.json", "config", "show", "--no-config"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                status, _, error = self.call(arguments, parse_exit=2)
                self.assertEqual(status, 2)
                self.assertIn("error:", error)

    def test_runtime_failures_report_stderr_and_restore_settings(self):
        previous = manager._MANAGER_CONFIG
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "snmp_get", side_effect=RuntimeError("request timed out")):
            status, output, error = self.call(["get", "--no-config", "--target", "device", "--oid", OID])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("request timed out", error)
        self.assertIs(manager._MANAGER_CONFIG, previous)

    def test_commands_only_require_their_actual_net_snmp_tools(self):
        with mock.patch.object(manager.shutil, "which", side_effect=lambda command, path=None: "/bin/snmpget" if command == "snmpget" else None), \
                mock.patch.object(manager, "snmp_get", return_value=RECORD):
            self.assertEqual(self.call(["get", "--no-config", "--target", "device", "--oid", OID])[0], 0)
            status, output, error = self.call(["walk", "--no-config", "--target", "device", "--oid", OID])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("snmpwalk", error)
        self.assertIn("dnf install net-snmp-utils", error)

    def test_custom_net_snmp_directory_and_windows_dependency_guidance(self):
        configuration = settings.ManagerConfig(snmp_bin=self.path / "Net SNMP", timeout=30, retries=2)
        with manager.manager_session(configuration), mock.patch.object(manager.shutil, "which", return_value="custom/snmpget.exe") as which:
            arguments = manager.build_common_args("snmpget", "2c", "public", "device")
            self.assertEqual(arguments[0], "custom/snmpget.exe")
            which.assert_called_once_with("snmpget", path=str(configuration.snmp_bin))
            with mock.patch.object(manager.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="value", stderr="")) as run:
                self.assertEqual(manager.run_command(arguments), "value")
                self.assertEqual(run.call_args.kwargs["timeout"], 95)
        with mock.patch.object(manager.sys, "platform", "win32"), mock.patch.object(manager.shutil, "which", return_value=None):
            status, _, error = self.call(["doctor", "--no-config"])
        self.assertEqual(status, 1)
        self.assertIn("--snmp-bin", error)

    def test_walk_set_browse_and_live_mib_dispatch_correctly(self):
        for operation, function in (("walk", "snmp_walk"), ("set", "snmp_set"), ("browse", "browse_device"), ("mib", "browse_device")):
            arguments = [operation, "--no-config", "--target", "device"]
            if operation in ("walk", "set"):
                arguments.extend(("--oid", OID))
            if operation == "set":
                arguments.extend(("--type", "s", "--value", "new value"))
            with self.subTest(operation=operation), mock.patch.object(manager, "check_dependencies", return_value=True), \
                    mock.patch.object(manager, function, return_value=RECORD if operation == "set" else [RECORD]) as request:
                self.assertEqual(self.call(arguments)[0], 0)
                self.assertEqual(request.call_args.args[0:3], ("device", "2c", "public"))
                if operation == "set":
                    self.assertEqual(request.call_args.args[-2:], ("s", "new value"))

    def test_cpu_and_memory_get_use_aggregate_helpers(self):
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "snmp_cpu_average", return_value=(25, [], 10, 40)), \
                mock.patch.object(manager, "get_physical_memory_snapshot", return_value={"usage_percent": 60}):
            for oid, value, kind in ((manager.CPU_LOAD_ROOT_OID, "25.000", "AggregateGauge"),
                                     (manager.PHYSICAL_MEMORY_ROOT_OID, "60.000", "PhysicalMemoryAggregate")):
                with self.subTest(oid=oid):
                    status, output, _ = self.call(["get", "--no-config", "--target", "device", "--oid", oid])
                    self.assertEqual(status, 0)
                    self.assertIn(f"{kind}: {value}", output)

    def test_capture_uses_profile_defaults_and_propagates_interrupt_status(self):
        profile = self.path / "profile.json"
        profile.write_text(json.dumps({"target": "device", "interval": 10, "counter_bits": "64"}), encoding="utf-8")
        with mock.patch.object(manager, "check_dependencies", return_value=True), \
                mock.patch.object(manager, "continuous_capture", return_value=130) as capture:
            status, _, _ = self.call(["capture", "--config", str(profile), "--oid", OID, "--output", str(self.path / "capture"),
                                     "--duration", "1", "--sync-agent"])
        self.assertEqual(status, 130)
        self.assertEqual(capture.call_args.args[4], 10)
        self.assertEqual(capture.call_args.kwargs, {"counter_bits": 64, "duration": 1.0, "sync_agent": True})


class ManagerProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-profile-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def test_invalid_fields_types_and_nonfinite_seconds_are_rejected(self):
        cases = (
            [], {"unknown": 1}, {"target": ""}, {"target": "-bad"}, {"community": None},
            {"version": "3"}, {"interval": "1"}, {"interval": True}, {"interval": 0},
            {"interval": -1}, {"timeout": float("nan")}, {"timeout": float("inf")},
            {"timeout": False}, {"retries": True}, {"retries": 1.5}, {"retries": -1},
            {"retries": 11}, {"counter_bits": 64}, {"snmp_bin": ""}, {"encryption_config": []},
        )
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ValueError):
                settings.config_from_document(document, self.path)

    def test_bom_and_relative_paths_are_portable(self):
        profile = self.path / "profile.json"
        document = {"snmp_bin": "Net SNMP/bin", "encryption_config": "keys/encryption.json", "interval": 120}
        profile.write_text(json.dumps(document), encoding="utf-8-sig")
        parsed = settings.load_manager_config(profile)
        self.assertEqual(parsed.snmp_bin, self.path / "Net SNMP" / "bin")
        self.assertEqual(parsed.encryption_config, self.path / "keys" / "encryption.json")
        self.assertEqual(parsed.interval, 120)

    def test_environment_profile_explicit_override_and_no_config(self):
        first, second = self.path / "first.json", self.path / "second.json"
        first.write_text('{"target": "environment-device"}', encoding="utf-8")
        second.write_text('{"target": "explicit-device"}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOOKAH_MANAGER_CONFIG": str(first)}):
            self.assertEqual(settings.load_manager_config().target, "environment-device")
            self.assertEqual(settings.load_manager_config(second).target, "explicit-device")
            self.assertIsNone(settings.load_manager_config(use_defaults=False).target)
        with self.assertRaises(FileNotFoundError):
            settings.load_manager_config(self.path / "missing.json")

    def test_malformed_profile_becomes_a_cli_error(self):
        profile = self.path / "bad.json"
        profile.write_text("not JSON", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(manager.main(["config", "show", "--config", str(profile)]), 1)
        self.assertIn("ERROR:", error.getvalue())


class HeadlessCliPlotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-headless-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.csv = self.path / "cpu.csv"
        rows = [{"elapsed_seconds": elapsed, "oid": manager.CPU_LOAD_ROOT_OID, "numeric_value": value,
                 "value_type": "AggregateGauge", "metric_kind": "cpu_average", "metric_unit": "%"}
                for elapsed, value in ((0, 20), (1, 40))]
        manager.append_rows(self.csv, manager.CAPTURE_FIELDS, rows)

    def call(self, arguments):
        with mock.patch("builtins.input", side_effect=AssertionError("CLI plots must not prompt")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            return manager.main(arguments), error.getvalue()

    def assert_png(self, path):
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(path.stat().st_size, 1000)

    def test_plot_saves_headless_png_and_creates_destination_directory(self):
        output = self.path / "with spaces" / "cpu.png"
        self.assertEqual(self.call(["plot", str(self.csv), "--output", str(output), "--no-show", "--color", "green"]), (0, ""))
        self.assert_png(output)

    def test_comparison_has_no_prompts_and_accepts_labels_and_title(self):
        output = self.path / "comparison.png"
        self.assertEqual(self.call(["compare", str(self.csv), str(self.csv), "--output", str(output),
                                    "--label-first", "Before", "--label-second", "After", "--title", "CPU"]), (0, ""))
        self.assert_png(output)

    def test_all_memory_graph_modes_save_valid_pngs(self):
        memory = self.path / "ram.csv"
        rows = [{"elapsed_seconds": elapsed, "memory_total_gib": 8, "memory_used_gib": used,
                 "memory_free_gib": 8 - used, "memory_usage_percent": used / 8 * 100}
                for elapsed, used in ((0, 2), (1, 3))]
        manager.append_rows(memory, manager.CAPTURE_FIELDS, rows)
        for mode in ("used", "percent", "both"):
            with self.subTest(mode=mode):
                output = self.path / f"ram-{mode}.png"
                self.assertEqual(self.call(["plot", str(memory), "--memory-mode", mode, "--output", str(output)]), (0, ""))
                self.assert_png(output)
        for mode in ("used", "percent"):
            with self.subTest(comparison_mode=mode):
                output = self.path / f"comparison-{mode}.png"
                self.assertEqual(self.call(["compare", str(memory), str(memory), "--memory-mode", mode, "--output", str(output)]), (0, ""))
                self.assert_png(output)

    def test_missing_empty_nonnumeric_and_incompatible_csvs_fail(self):
        empty, text = self.path / "empty.csv", self.path / "text.csv"
        empty.write_text("numeric_value\n", encoding="utf-8")
        text.write_text("numeric_value\nnot-a-number\n", encoding="utf-8")
        for path in (self.path / "missing.csv", empty, text):
            with self.subTest(path=path):
                status, error = self.call(["plot", str(path)])
                self.assertEqual(status, 1)
                self.assertIn("ERROR:", error)
        memory = self.path / "memory.csv"
        manager.append_rows(memory, manager.CAPTURE_FIELDS, [{"elapsed_seconds": 0, "memory_total_gib": 8,
                                                             "memory_used_gib": 2, "memory_usage_percent": 25}])
        status, error = self.call(["compare", str(self.csv), str(memory)])
        self.assertEqual(status, 1)
        self.assertIn("physical-memory", error)
