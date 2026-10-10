"""Encrypted Agent/Manager SNMP requests, configuration, capture, and wire checks."""

import contextlib
import csv
import io
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from pyasn1.codec.ber import encoder
from pysnmp.proto import rfc1902
from pysnmp.proto.api import v2c

import snmp_agent
import snmp_crypto as crypto
import snmp_data_collector_v12 as manager
from tests.helpers import (READ_COMMUNITY, WRITE_COMMUNITY, TOOLS, RunningAgent,
                           loopback_traffic, run_client, run_executable)


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "Net-SNMP clients required")
class EncryptedIntegrationTests(unittest.TestCase):
    integration_test = True

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="hookah-encrypted-integration-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.keys = crypto.generate_key_bundle(Path(cls.directory.name) / "keys", 2048)
        cls.encryption = {"enabled": True, "private_key_file": str(cls.keys["private_key"]),
                          "peer_public_key_file": None}
        cls.agent = RunningAgent(encryption=cls.encryption)
        cls.addClassCleanup(cls.agent.close)
        cls.agent.start()

    def setUp(self):
        manager.configure_encryption(self.keys["encryption_config"])
        self.addCleanup(manager.configure_encryption)

    def test_get_walk_and_set_roundtrip_in_both_snmp_versions(self):
        for version in ("1", "2c"):
            with self.subTest(version=version):
                record = manager.snmp_get(self.agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.1.1.0")
                self.assertIn("Python SNMP host agent", record[2])
                rows = manager.snmp_walk(self.agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
                self.assertTrue(rows)
                manager.snmp_set(self.agent.target, version, WRITE_COMMUNITY, ".1.3.6.1.2.1.1.6.0", "s", "Encrypted lab")
                self.assertEqual(manager.snmp_get(self.agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.1.6.0")[2].strip('"'),
                                 "Encrypted lab")

    def test_encrypted_agent_rejects_plaintext_without_fallback(self):
        result = run_client(self.agent, "snmpget", ".1.3.6.1.2.1.1.1.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timeout", result.stderr)
        self.assertIn("Python SNMP host agent",
                      manager.snmp_get(self.agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.1.1.0")[2])

    def test_remote_configuration_cli_is_encrypted_and_persistent(self):
        result = run_executable(
            "snmp_agent.py", "config", "set", "--remote", self.agent.target,
            "--community", WRITE_COMMUNITY, "--encryption-config", self.keys["encryption_config"],
            "--interval", "ifInOctets", "0.15", cwd=self.agent.path,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["collection_intervals"]["ifInOctets"], 0.15)
        saved = json.loads(self.agent.config_file.read_text(encoding="utf-8"))
        self.assertTrue(saved["encryption"]["enabled"])
        self.assertEqual(saved["collection_intervals"]["ifInOctets"], 0.15)
        shown = run_executable(
            "snmp_agent.py", "config", "show", "--remote", self.agent.target, "--community", READ_COMMUNITY,
            "--encryption-config", self.keys["encryption_config"], cwd=self.agent.path,
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn("private_key_file", json.loads(shown.stdout))

    def test_encrypted_cli_displays_banner_and_synchronized_capture_writes_rates(self):
        rows = manager.snmp_walk(self.agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
        loopback = next((row for row in rows if row[2].strip('"') in ("lo", "lo0")), None)
        if loopback is None:
            self.skipTest("Named loopback required")
        oid = ".1.3.6.1.2.1.2.2.1.10." + loopback[0].split(".")[-1]
        output = self.agent.path / "encrypted.csv"
        with loopback_traffic():
            result = run_executable(
                "snmp_data_collector_v12.py", "--banner", "capture", "--target", self.agent.target,
                "--community", READ_COMMUNITY, "--oid", oid, "--sync-agent", "--duration", "0.8",
                "--output", output, "--encryption-config", self.keys["encryption_config"], cwd=self.agent.path,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Hookah", result.stdout)
        with output.open(newline="", encoding="utf-8") as source:
            data = list(csv.DictReader(source))
        self.assertTrue(any(row["status"] == "updated" for row in data))
        self.assertTrue(any(float(row["rate"]) > 0 for row in data if row["rate"]))

    def test_wrong_key_and_replayed_encrypted_request_get_no_response(self):
        packet = v2c.GetRequestPDU()
        v2c.apiPDU.set_defaults(packet)
        v2c.apiPDU.set_varbinds(packet, [(rfc1902.ObjectIdentifier("1.3.6.1.2.1.1.1.0"), rfc1902.Null(""))])
        message = v2c.Message()
        v2c.apiMessage.set_defaults(message)
        v2c.apiMessage.set_community(message, READ_COMMUNITY)
        v2c.apiMessage.set_pdu(message, packet)
        raw = encoder.encode(message)
        sender = crypto.RsaEnvelope.from_settings(crypto.load_encryption_settings(self.keys["encryption_config"]))
        envelope, _ = sender.encrypt(raw)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.3)
            target = crypto.parse_udp_target(self.agent.target)
            client.sendto(envelope, target)
            reply, _ = client.recvfrom(crypto.MAX_DATAGRAM)
            self.assertTrue(reply.startswith(crypto.MAGIC))
            client.sendto(envelope, target)
            with self.assertRaises(socket.timeout):
                client.recvfrom(crypto.MAX_DATAGRAM)
            wrong_private = crypto.crypto_backend().rsa.generate_private_key(public_exponent=65537, key_size=2048)
            bad, _ = crypto.RsaEnvelope(wrong_private).encrypt(raw)
            client.sendto(bad, target)
            with self.assertRaises(socket.timeout):
                client.recvfrom(crypto.MAX_DATAGRAM)

    def test_wire_proxy_observes_only_encrypted_request_and_response(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
            relay.bind(("127.0.0.1", 0))
            upstream.connect(crypto.parse_udp_target(self.agent.target))
            relay.settimeout(3)
            upstream.settimeout(3)
            captured = []
            errors = []

            def forward():
                try:
                    request, source = relay.recvfrom(crypto.MAX_DATAGRAM)
                    captured.append(request)
                    upstream.send(request)
                    response = upstream.recv(crypto.MAX_DATAGRAM)
                    captured.append(response)
                    relay.sendto(response, source)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=forward)
            worker.start()
            try:
                target = f"udp:127.0.0.1:{relay.getsockname()[1]}"
                record = manager.snmp_get(target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.1.1.0")
                self.assertIn("Python SNMP host agent", record[2])
            finally:
                worker.join(timeout=4)
            self.assertFalse(errors, errors)
            self.assertEqual(len(captured), 2)
            self.assertTrue(all(packet.startswith(crypto.MAGIC) for packet in captured))
            self.assertTrue(all(READ_COMMUNITY.encode("ascii") not in packet for packet in captured))

    def test_external_stop_event_stops_service_loop_without_signals(self):
        # Exercise the Windows service stop mechanism against the real Agent loop.
        import asyncio
        from dataclasses import replace
        stop = threading.Event()
        configuration = snmp_agent.config_from_document(snmp_agent.read_config_document(self.agent.config_file),
                                                       self.agent.config_file.parent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        configuration = replace(configuration, port=port)
        metrics = snmp_agent.HostMetrics(configuration)

        async def scenario():
            task = asyncio.create_task(snmp_agent.serve(configuration, metrics, external_stop=stop,
                                                       install_signal_handlers=False))
            await asyncio.sleep(0.1)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(scenario())
