"""Disposable agents, executable CLI helpers, and loopback traffic generation."""

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
READ_COMMUNITY = "agent-test-read"
WRITE_COMMUNITY = "agent-test-write"
TOOLS = ("snmpget", "snmpwalk", "snmpbulkwalk", "snmpset")


def executable_environment():
    environment = dict(os.environ)
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["MPLBACKEND"] = "Agg"
    return environment


def run_client(agent, command, *arguments, version="2c", community=READ_COMMUNITY):
    return subprocess.run(
        [command, "-v", version, "-c", community, "-On", "-Oa", "-t", "1", "-r", "0",
         agent.target, *arguments],
        capture_output=True, text=True, timeout=15,
    )


def run_executable(name, *arguments, cwd=None, input=None, timeout=15):
    command = [str(ROOT / name), *map(str, arguments)]
    if os.name == "nt":
        command.insert(0, sys.executable)
    return subprocess.run(
        command, env=executable_environment(),
        cwd=cwd, input=input, capture_output=True, text=True, timeout=timeout,
    )


class RunningAgent:
    def __init__(self, write_community=WRITE_COMMUNITY, sample_interval=0.15, collection_intervals=None,
                 encryption=None):
        self.directory = tempfile.TemporaryDirectory(prefix="snmp-agent-test-")
        self.path = Path(self.directory.name)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.target = f"udp:127.0.0.1:{self.port}"
        self.state_file = self.path / "state.json"
        self.config_file = self.path / "config.json"
        self.config_file.write_text(json.dumps({
            "listen_address": "127.0.0.1", "port": self.port,
            "read_community": READ_COMMUNITY, "write_community": write_community,
            "allowed_managers": ["127.0.0.1/32"], "sample_interval": sample_interval,
            "collection_intervals": collection_intervals or {},
            "sys_name": "test-device", "sys_contact": "Test administrator",
            "sys_location": "Test lab", "state_file": str(self.state_file),
            "encryption": encryption or {"enabled": False, "private_key_file": None, "peer_public_key_file": None},
        }), encoding="utf-8")
        self.encryption_config = None
        self.cipher = None
        if encryption and encryption.get("enabled"):
            from snmp_crypto import RsaEnvelope, EncryptionSettings
            self.encryption_config = self.path / "client_encryption.json"
            self.encryption_config.write_text(json.dumps(encryption), encoding="utf-8")
            self.cipher = RsaEnvelope.from_settings(EncryptionSettings.from_document(encryption))
        self.process = None

    def start(self):
        command = [str(ROOT / "snmp_agent.py"), "--config", str(self.config_file)]
        if os.name == "nt":
            command.insert(0, sys.executable)
        self.process = subprocess.Popen(
            command,
            cwd=self.path, env=executable_environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.process.poll() is None:
            if self.cipher:
                from snmp_crypto import EncryptedSnmpProxy
                with EncryptedSnmpProxy(self.target, self.cipher) as proxy:
                    response = run_client(SimpleNamespace(target=proxy.snmp_target), "snmpget", ".1.3.6.1.2.1.1.1.0")
            else:
                response = run_client(self, "snmpget", ".1.3.6.1.2.1.1.1.0")
            if response.returncode == 0:
                return self
            time.sleep(0.05)
        self.stop()
        raise RuntimeError(f"Agent failed to start: {self.output}")

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.output, _ = self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.output, _ = self.process.communicate(timeout=5)

    def close(self):
        self.stop()
        self.directory.cleanup()

    def __enter__(self):
        try:
            return self.start()
        except Exception:
            self.close()
            raise

    def __exit__(self, *exception):
        self.close()


@contextlib.contextmanager
def loopback_traffic():
    stop = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(("127.0.0.1", 0))
        destination = sink.getsockname()

        def generate():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                while not stop.is_set():
                    sender.sendto(b"x" * 1024, destination)
                    stop.wait(0.01)

        worker = threading.Thread(target=generate)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=2)


def wait_for(predicate, timeout=3, period=0.03):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(period)
    raise AssertionError("Condition did not become true before the timeout.")
