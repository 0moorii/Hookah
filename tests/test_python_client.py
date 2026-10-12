"""Bundled SNMP backend formatting, SET types, and real protocol interoperability."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pysnmp.proto import rfc1902, rfc1905

import snmp_data_collector_v12 as manager
import snmp_python_client as client
from snmp_manager_config import ManagerConfig
from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent


OID = ".1.3.6.1.2.1.1.5.0"


class PythonClientUnitTests(unittest.TestCase):
    def test_targets_validate_hostname_and_udp_ports(self):
        for text, expected in (("device", ("device", 161)), ("udp:127.0.0.1:1161", ("127.0.0.1", 1161)),
                               ("device:22161", ("device", 22161))):
            with self.subTest(text=text):
                self.assertEqual(client.parse_target(text), expected)
        for text in ("", ":1161", "device:0", "device:65536", "device:invalid"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                client.parse_target(text)

    def test_wire_values_are_compatible_with_manager_parsing(self):
        values = ((rfc1902.Integer32(-5), -5), (rfc1902.Gauge32(7), 7),
                  (rfc1902.Counter32((1 << 32) - 1), (1 << 32) - 1),
                  (rfc1902.Counter64((1 << 64) - 1), (1 << 64) - 1),
                  (rfc1902.TimeTicks(12345), 12345), (rfc1902.OctetString("0.42"), 0.42))
        for value, expected in values:
            with self.subTest(value=value):
                line = client.format_binding(rfc1902.ObjectIdentifier(OID), value)
                self.assertEqual(manager.parse_snmp_line(line)[3], expected)
        binary = client.format_binding(rfc1902.ObjectIdentifier(OID), rfc1902.OctetString(b"\x00\xff"))
        self.assertIn("Hex-STRING: 00 FF", binary)
        text = client.format_binding(rfc1902.ObjectIdentifier(OID), rfc1902.OctetString("Café".encode()))
        self.assertIn("Café", text)
        self.assertIn("OID:", client.format_binding(rfc1902.ObjectIdentifier(OID), rfc1902.ObjectIdentifier(OID)))
        self.assertIn("IpAddress: 127.0.0.1", client.format_binding(rfc1902.ObjectIdentifier(OID), rfc1902.IpAddress("127.0.0.1")))
        self.assertIsNone(client.format_binding(rfc1902.ObjectIdentifier(OID), rfc1905.endOfMibView))

    def test_all_supported_set_types_are_encoded_and_invalid_types_fail(self):
        for kind, text, expected in (("i", "-2", -2), ("u", "3", 3), ("t", "4", 4),
                                      ("c", "5", 5), ("U", "18446744073709551615", (1 << 64) - 1)):
            with self.subTest(kind=kind):
                self.assertEqual(int(client.set_value(kind, text)), expected)
        self.assertEqual(client.set_value("s", "Café").asOctets(), "Café".encode())
        self.assertEqual(client.set_value("x", "01 ff").asOctets(), b"\x01\xff")
        self.assertEqual(client.set_value("d", "1 255").asOctets(), b"\x01\xff")
        self.assertEqual(client.set_value("a", "127.0.0.1").prettyPrint(), "127.0.0.1")
        self.assertEqual(tuple(client.set_value("o", OID)), tuple(map(int, OID.lstrip(".").split("."))))
        with self.assertRaises(ValueError):
            client.set_value("invalid", "1")

    def test_packaged_manager_does_not_require_external_clients(self):
        with mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), manager.manager_session(ManagerConfig()), \
                mock.patch.object(manager.shutil, "which", side_effect=AssertionError("unexpected external lookup")):
            self.assertTrue(manager.check_dependencies())
            self.assertIn("bundled PySNMP", manager.snmp_tool("snmpget"))
        with mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), \
                manager.manager_session(ManagerConfig(snmp_bin=Path("custom clients"))), \
                mock.patch.object(manager.shutil, "which", return_value="custom/snmpget"):
            self.assertFalse(manager.python_snmp_backend())
            self.assertEqual(manager.snmp_tool("snmpget"), "custom/snmpget")

    def test_bundled_command_backend_propagates_errors(self):
        with mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), manager.manager_session(ManagerConfig()), \
                mock.patch.object(client, "run_client", side_effect=RuntimeError("device timed out")):
            with self.assertRaisesRegex(RuntimeError, "device timed out"):
                manager.run_command(manager.build_common_args("snmpget", "2c", "public", "device") + [OID])


@unittest.skipUnless(shutil.which("snmpget"), "Net-SNMP reference client is required to start the test Agent")
class PythonClientIntegrationTests(unittest.TestCase):
    integration_test = True

    def test_bundled_get_walk_and_authorized_set_work_in_both_versions(self):
        with RunningAgent() as agent, mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), \
                manager.manager_session(ManagerConfig()):
            for version in ("1", "2c"):
                with self.subTest(version=version):
                    record = manager.snmp_get(agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.1.1.0")
                    self.assertIn("Python SNMP host agent", record[2])
                    rows = manager.snmp_walk(agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
                    self.assertTrue(rows)
                    manager.snmp_set(agent.target, version, WRITE_COMMUNITY, OID, "s", "Bundled Café")
                    self.assertIn("Bundled Café", manager.snmp_get(agent.target, version, READ_COMMUNITY, OID)[2])
                    manager.snmp_set(agent.target, version, WRITE_COMMUNITY, OID, "s", "--literal-value")
                    self.assertIn("--literal-value", manager.snmp_get(agent.target, version, READ_COMMUNITY, OID)[2])
                    with self.assertRaises(RuntimeError):
                        manager.snmp_set(agent.target, version, READ_COMMUNITY, OID, "s", "Not authorized")
                    self.assertGreaterEqual(manager.snmp_cpu_average(agent.target, version, READ_COMMUNITY)[0], 0)
                    self.assertGreater(manager.get_physical_memory_snapshot(agent.target, version, READ_COMMUNITY)["total_bytes"], 0)

    def test_bundled_walk_stays_ordered_and_preserves_counter64(self):
        with RunningAgent() as agent, mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), \
                manager.manager_session(ManagerConfig()):
            rows = manager.snmp_walk(agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.31")
            numbers = [tuple(map(int, row[0].lstrip(".").split("."))) for row in rows]
            self.assertEqual(numbers, sorted(set(numbers)))
            self.assertTrue(any(row[1] == "Counter64" and type(row[3]) is int for row in rows))

    def test_bundled_client_supports_the_encrypted_transport_without_external_tools(self):
        from snmp_crypto import generate_key_bundle
        with tempfile.TemporaryDirectory(prefix="hookah-client-encrypted-") as directory:
            keys = generate_key_bundle(Path(directory) / "keys", 2048)
            encryption = {"enabled": True, "private_key_file": str(keys["private_key"]), "peer_public_key_file": None}
            with RunningAgent(encryption=encryption) as agent, \
                    mock.patch.dict(os.environ, {"HOOKAH_SNMP_BACKEND": "python"}), manager.manager_session(ManagerConfig()):
                manager.configure_encryption(keys["encryption_config"])
                for version in ("1", "2c"):
                    with self.subTest(version=version):
                        self.assertIn("Python SNMP host agent", manager.snmp_get(agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.1.1.0")[2])
                        self.assertTrue(manager.snmp_walk(agent.target, version, READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2"))
                        manager.snmp_set(agent.target, version, WRITE_COMMUNITY, OID, "s", "Encrypted bundled client")
