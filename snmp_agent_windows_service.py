#!/usr/bin/env python3
"""Native Windows 10/11 service host and install/start/stop/remove CLI."""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import threading
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace


SERVICE_NAME = "HookahSnmpAgent"
SERVICE_DISPLAY_NAME = "Hookah SNMP Agent"
SERVICE_DESCRIPTION = "Collects host metrics and serves optional RSA-encrypted SNMP on the local network."
REGISTRY_PARAMETERS = rf"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}\Parameters"


def data_directory(program_data=None):
    return Path(program_data or os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Hookah" / "Agent"


def windows_dependencies():
    if sys.platform != "win32":
        raise RuntimeError("Windows service commands must run on Windows 10/11.")
    try:
        import servicemanager
        import win32service
        import win32serviceutil
        import winreg
    except ImportError as exc:
        raise RuntimeError("Install requirements-windows-agent.txt using the service's Python environment.") from exc
    return SimpleNamespace(servicemanager=servicemanager, service=win32service,
                           utility=win32serviceutil, registry=winreg)


def service_config_path(api):
    try:
        with api.registry.OpenKey(api.registry.HKEY_LOCAL_MACHINE, REGISTRY_PARAMETERS) as key:
            value, _ = api.registry.QueryValueEx(key, "ConfigFile")
        return Path(value)
    except FileNotFoundError:
        return data_directory() / "snmp_agent_config.json"


def set_service_config_path(api, path):
    with api.registry.CreateKeyEx(api.registry.HKEY_LOCAL_MACHINE, REGISTRY_PARAMETERS,
                                  0, api.registry.KEY_SET_VALUE) as key:
        api.registry.SetValueEx(key, "ConfigFile", 0, api.registry.REG_SZ, str(path))


def run_service_agent(config_path, stop_requested):
    from snmp_agent import HostMetrics, config_from_document, read_config_document, serve

    config_path = Path(config_path).resolve()
    settings = config_from_document(read_config_document(config_path), config_path.parent)
    if settings.state_file is None:
        settings = replace(settings, state_file=config_path.parent / "state" / "system.json")
    logs = config_path.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "agent.log", maxBytes=2 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        metrics = HostMetrics(settings, config_path=config_path)
        asyncio.run(serve(settings, metrics, external_stop=stop_requested, install_signal_handlers=False))
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        handler.close()


def create_service_class(api):
    class HookahSnmpAgentService(api.utility.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            self.stop_requested = threading.Event()
            super().__init__(args)

        def SvcStop(self):
            self.ReportServiceStatus(api.service.SERVICE_STOP_PENDING)
            self.stop_requested.set()

        def SvcDoRun(self):
            api.servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
            try:
                run_service_agent(service_config_path(api), self.stop_requested)
            except Exception as exc:
                api.servicemanager.LogErrorMsg(f"{SERVICE_NAME} failed: {exc}")
                raise
            finally:
                api.servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")

    HookahSnmpAgentService.__qualname__ = "HookahSnmpAgentService"
    return HookahSnmpAgentService


def install_service(api, config_path, startup="auto", python_executable=None, script_path=None):
    """Use the venv's Python directly, avoiding pythonservice.exe DLL/venv issues."""
    from snmp_agent import config_from_document, read_config_document
    from snmp_crypto import EncryptionSettings, RsaEnvelope

    config_path = Path(config_path).expanduser().resolve()
    settings = config_from_document(read_config_document(config_path), config_path.parent)
    RsaEnvelope.from_settings(EncryptionSettings.from_document(settings.encryption))
    # Preserve a virtualenv executable path even when it is a symlink.
    executable = str(Path(python_executable or sys.executable).absolute())
    script = str(Path(script_path or __file__).resolve())
    api.utility.InstallService(
        "snmp_agent_windows_service.HookahSnmpAgentService", SERVICE_NAME, SERVICE_DISPLAY_NAME,
        startType=api.service.SERVICE_AUTO_START if startup == "auto" else api.service.SERVICE_DEMAND_START,
        exeName=executable, exeArgs=subprocess.list2cmdline([script, "run"]),
        userName=r"NT AUTHORITY\LocalService", password=None, description=SERVICE_DESCRIPTION,
    )
    try:
        set_service_config_path(api, config_path)
    except Exception:
        # Roll back only the service this invocation just created.
        api.utility.RemoveService(SERVICE_NAME)
        raise


def service_action(api, action):
    if action == "start":
        api.utility.StartService(SERVICE_NAME)
        api.utility.WaitForServiceStatus(SERVICE_NAME, api.service.SERVICE_RUNNING, 30)
    elif action == "stop":
        api.utility.StopService(SERVICE_NAME)
        api.utility.WaitForServiceStatus(SERVICE_NAME, api.service.SERVICE_STOPPED, 30)
    elif action == "restart":
        service_action(api, "stop")
        service_action(api, "start")
    elif action == "remove":
        state = api.utility.QueryServiceStatus(SERVICE_NAME)[1]
        if state != api.service.SERVICE_STOPPED:
            service_action(api, "stop")
        api.utility.RemoveService(SERVICE_NAME)
    elif action == "status":
        return api.utility.QueryServiceStatus(SERVICE_NAME)[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "start", "stop", "restart", "status", "remove", "run"))
    parser.add_argument("--config", type=Path, default=data_directory() / "snmp_agent_config.json")
    parser.add_argument("--startup", choices=("auto", "manual"), default="auto")
    args = parser.parse_args(argv)
    try:
        api = windows_dependencies()
        if args.action == "install":
            install_service(api, args.config, args.startup)
        elif args.action == "run":
            service_class = create_service_class(api)
            globals()["HookahSnmpAgentService"] = service_class
            api.servicemanager.Initialize()
            api.servicemanager.PrepareToHostSingle(service_class)
            api.servicemanager.StartServiceCtrlDispatcher()
        else:
            state = service_action(api, args.action)
            if args.action == "status":
                print(f"{SERVICE_NAME}: Windows service state {state}")
        return 0
    except Exception as exc:
        print(f"Windows service error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
