"""Windows service lifecycle checks using mocked SCM/registry APIs on any OS."""

import asyncio
import contextlib
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import snmp_agent
import snmp_agent_windows_service as service
import build_agent_package


class FakeServiceFramework:
    def __init__(self, args):
        self.args = args
        self.statuses = []

    def ReportServiceStatus(self, status):
        self.statuses.append(status)


def windows_api(config_path):
    return SimpleNamespace(
        service=SimpleNamespace(SERVICE_AUTO_START=2, SERVICE_DEMAND_START=3,
                                SERVICE_RUNNING=4, SERVICE_STOP_PENDING=3, SERVICE_STOPPED=1),
        utility=SimpleNamespace(ServiceFramework=FakeServiceFramework, InstallService=mock.Mock(),
                                RemoveService=mock.Mock(), StartService=mock.Mock(), StopService=mock.Mock(),
                                WaitForServiceStatus=mock.Mock(), QueryServiceStatus=mock.Mock(return_value=(0, 1))),
        servicemanager=SimpleNamespace(LogInfoMsg=mock.Mock(), LogErrorMsg=mock.Mock(), Initialize=mock.Mock(),
                                       PrepareToHostSingle=mock.Mock(), StartServiceCtrlDispatcher=mock.Mock()),
        registry=SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_SET_VALUE=2, REG_SZ=1,
                                 OpenKey=mock.MagicMock(), CreateKeyEx=mock.MagicMock(), SetValueEx=mock.Mock(),
                                 QueryValueEx=mock.Mock(return_value=(str(config_path), 1))),
    )


class WindowsServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-service-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.config = self.path / "snmp_agent_config.json"
        snmp_agent.write_json_atomic(self.config, snmp_agent.config_document(snmp_agent.AgentConfig(port=1161)))
        self.api = windows_api(self.config)

    def test_install_uses_virtualenv_python_localservice_autostart_and_quoted_script(self):
        python = self.path / "Python Environment" / "python.exe"
        script = self.path / "Hookah Agent" / "snmp_agent_windows_service.py"
        service.install_service(self.api, self.config, python_executable=python, script_path=script)
        args, kwargs = self.api.utility.InstallService.call_args
        self.assertEqual(args[1], service.SERVICE_NAME)
        self.assertEqual(kwargs["exeName"], str(python))
        self.assertEqual(kwargs["userName"], r"NT AUTHORITY\LocalService")
        self.assertEqual(kwargs["startType"], self.api.service.SERVICE_AUTO_START)
        self.assertEqual(kwargs["exeArgs"], f'"{script}" run')
        self.api.registry.SetValueEx.assert_called_once()

    def test_registry_failure_rolls_back_the_new_service(self):
        self.api.registry.SetValueEx.side_effect = OSError("registry permission denied")
        with self.assertRaises(OSError):
            service.install_service(self.api, self.config)
        self.api.utility.RemoveService.assert_called_once_with(service.SERVICE_NAME)

    def test_stop_sets_external_stop_event_and_reports_pending(self):
        instance = service.create_service_class(self.api)([service.SERVICE_NAME])
        instance.SvcStop()
        self.assertTrue(instance.stop_requested.is_set())
        self.assertEqual(instance.statuses, [self.api.service.SERVICE_STOP_PENDING])

    def test_service_reads_registry_configuration_and_records_failures(self):
        instance = service.create_service_class(self.api)([service.SERVICE_NAME])
        with mock.patch.object(service, "run_service_agent") as runner:
            instance.SvcDoRun()
            runner.assert_called_once_with(self.config, instance.stop_requested)
        with mock.patch.object(service, "run_service_agent", side_effect=OSError("invalid key path")):
            with self.assertRaises(OSError):
                instance.SvcDoRun()
        self.api.servicemanager.LogErrorMsg.assert_called_once()

    def test_start_stop_restart_remove_use_scm_waits(self):
        service.service_action(self.api, "restart")
        self.api.utility.StopService.assert_called_once_with(service.SERVICE_NAME)
        self.api.utility.StartService.assert_called_once_with(service.SERVICE_NAME)
        self.assertEqual(self.api.utility.WaitForServiceStatus.call_count, 2)
        service.service_action(self.api, "remove")
        self.api.utility.RemoveService.assert_called_once_with(service.SERVICE_NAME)

    def test_service_host_uses_external_stop_without_console_signal_handlers(self):
        stop = threading.Event()
        with mock.patch.object(snmp_agent, "HostMetrics") as metrics, \
                mock.patch.object(snmp_agent, "serve", new_callable=mock.AsyncMock) as serve:
            service.run_service_agent(self.config, stop)
        self.assertTrue((self.path / "logs" / "agent.log").exists())
        args, kwargs = serve.call_args
        self.assertEqual(args[0].state_file, self.path / "state" / "system.json")
        self.assertIs(kwargs["external_stop"], stop)
        self.assertFalse(kwargs["install_signal_handlers"])
        metrics.assert_called_once()

    def test_windows_style_interfaces_with_empty_flags_use_available_status(self):
        from tests.test_agent_unit import CollectionTests
        fixture = CollectionTests(methodName="test_get_requests_do_not_trigger_host_collection")
        try:
            fixture.setUp()
            with mock.patch.object(snmp_agent.socket, "if_nameindex", side_effect=OSError("not available")), \
                    mock.patch.object(snmp_agent.psutil, "net_if_stats", return_value={
                        "Ethernet": SimpleNamespace(isup=True, flags="", speed=1000, mtu=1500),
                    }), \
                    mock.patch.object(snmp_agent.psutil, "net_if_addrs", return_value={"Ethernet": []}), \
                    mock.patch.object(snmp_agent.psutil, "net_io_counters", return_value={"Ethernet": fixture.io}):
                fixture.metrics.refresh(force=True)
                index = fixture.metrics.interface_indexes["Ethernet"]
                self.assertEqual(int(fixture.metrics.snapshot[snmp_agent.IF_TABLE + (7, index)]), 1)
                self.assertEqual(int(fixture.metrics.snapshot[snmp_agent.IF_TABLE + (8, index)]), 1)
                self.assertEqual(int(fixture.metrics.snapshot[snmp_agent.IF_TABLE + (5, index)]), 1_000_000_000)
        finally:
            fixture.doCleanups()

    def test_native_service_commands_are_rejected_on_other_platforms(self):
        with mock.patch.object(service.sys, "platform", "linux"), self.assertRaises(RuntimeError):
            service.windows_dependencies()

    def test_unavailable_os_network_apis_do_not_stop_other_metrics(self):
        from tests.test_agent_unit import CollectionTests
        fixture = CollectionTests(methodName="test_get_requests_do_not_trigger_host_collection")
        try:
            fixture.setUp()
            with mock.patch.object(snmp_agent.psutil, "net_io_counters", side_effect=RuntimeError("GetAdaptersAddresses syscall failed")), \
                    mock.patch.object(snmp_agent.psutil, "net_if_stats", side_effect=OSError("not available")), \
                    mock.patch.object(snmp_agent.psutil, "net_if_addrs", side_effect=NotImplementedError):
                fixture.metrics.refresh(force=True)
            self.assertIn(snmp_agent.SYSTEM + (1, 0), fixture.metrics.snapshot)
            self.assertIn((1, 3, 6, 1, 2, 1, 25, 2, 2, 0), fixture.metrics.snapshot)
            self.assertNotIn((1, 3, 6, 1, 2, 1, 2, 1, 0), fixture.metrics.snapshot)
        finally:
            fixture.doCleanups()

    def test_disabled_windows_performance_counters_are_not_missing_dependencies(self):
        with mock.patch.object(snmp_agent.psutil, "swap_memory", side_effect=RuntimeError("Performance counters may be disabled")):
            self.assertIsNone(snmp_agent.optional_psutil("swap_memory"))

    def test_transfer_package_has_service_installer_and_application_dependencies(self):
        output = build_agent_package.build_package(self.path / "Hookah-Agent-Windows.zip")
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertIn("Hookah-Agent.exe", names)
            self.assertIn(".hookah/app/snmp_agent_windows_service.py", names)
            self.assertIn(".hookah/app/snmp_crypto.py", names)
            self.assertIn(".hookah/platform/windows/install-agent.ps1", names)
            self.assertIn(".hookah/dependencies/requirements-windows-agent.txt", names)
            self.assertIn(".hookah/docs/WINDOWS-AGENT.md", names)
            self.assertFalse(any((name.endswith(".pem") and not name.startswith(".hookah/environment/"))
                                 or name.startswith(".venv") for name in names))
