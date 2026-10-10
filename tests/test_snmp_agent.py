"""Protocol-level checks with real Net-SNMP clients and the existing manager."""

import contextlib
import csv
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, TOOLS, RunningAgent, run_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil
from pyasn1.codec.ber import encoder
from pysnmp.proto import rfc1902
from pysnmp.proto.api import v2c

import snmp_agent
import snmp_data_collector_v12 as manager
import snmp_mib as mib
from tests.helpers import run_executable


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "Net-SNMP client tools required")
class ProtocolTests(unittest.TestCase):
    integration_test = True
    @classmethod
    def setUpClass(cls):
        cls.agent = RunningAgent()
        cls.addClassCleanup(cls.agent.close)
        cls.agent.start()

    def successful_client(self, command, *arguments, **options):
        result = run_client(self.agent, command, *arguments, **options)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def get(self, oid, version="2c"):
        return manager.snmp_get(self.agent.target, version, READ_COMMUNITY, oid)

    def test_system_get_supports_v1_and_v2c(self):
        for version in ("1", "2c"):
            with self.subTest(version=version):
                record = self.get(".1.3.6.1.2.1.1.1.0", version)
                self.assertEqual(record[1], "STRING")
                self.assertIn("Python SNMP host agent", record[2])
                uptime = self.get(".1.3.6.1.2.1.1.3.0", version)
                self.assertEqual(uptime[1], "Timeticks")
                self.assertIsInstance(uptime[3], int)
                self.assertGreaterEqual(uptime[3], 0)
        self.assertIn("1.3.6.1.4.1.32473.1.1", self.get(".1.3.6.1.2.1.1.2.0")[2])

    def test_manager_cpu_walk_and_average(self):
        for version in ("1", "2c"):
            with self.subTest(version=version):
                average, cores, minimum, maximum = manager.snmp_cpu_average(
                    self.agent.target, version, READ_COMMUNITY,
                )
                self.assertEqual(len(cores), psutil.cpu_count(logical=True))
                self.assertEqual(len({core["index"] for core in cores}), len(cores))
                self.assertLessEqual(minimum, average)
                self.assertLessEqual(average, maximum)
                self.assertGreaterEqual(minimum, 0)
                self.assertLessEqual(maximum, 100)

    def test_manager_memory_discovery_and_snapshot(self):
        for version in ("1", "2c"):
            with self.subTest(version=version):
                memory = manager.get_physical_memory_snapshot(self.agent.target, version, READ_COMMUNITY)
                self.assertEqual(memory["index"], 1)
                self.assertEqual(memory["description"], "Physical Memory")
                self.assertEqual(memory["storage_type_oid"], "1.3.6.1.2.1.25.2.1.2")
                self.assertLess(abs(memory["total_bytes"] - psutil.virtual_memory().total),
                                memory["allocation_unit_bytes"])
                self.assertGreater(memory["total_gib"], 0)
                self.assertGreaterEqual(memory["used_bytes"], 0)
                self.assertLessEqual(memory["used_bytes"], memory["total_bytes"])
                self.assertEqual(memory["used_bytes"] + memory["free_bytes"], memory["total_bytes"])
                self.assertAlmostEqual(memory["usage_percent"] + memory["free_percent"], 100)

    def test_compatible_catalog_gets_and_total_packet_oids(self):
        interfaces = manager.snmp_walk(self.agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
        self.assertTrue(interfaces)
        self.assertTrue(any(record[2].strip('"') in psutil.net_if_stats() for record in interfaces))
        index = interfaces[0][0].split(".")[-1]
        for item in manager.COMMON_OIDS:
            if int(item["number"]) > 25:
                continue  # Extended indexed/optional objects have separate discovery tests.
            if item["number"] in {"6", "7", "15", "25"}:
                continue
            with self.subTest(catalog=item["number"]):
                selected_index = "1" if item.get("index_label") == "processor index" else index
                record = self.get(item["template"].replace("{index}", selected_index))
                self.assertNotIn("No Such", record[2])
        for direction in (1, 2):
            record = self.get(f".1.3.6.1.4.1.32473.1.2.1.{direction}.{index}")
            self.assertEqual(record[1], "Counter32")
            self.assertGreaterEqual(record[3], 0)
        # A total-packet counter must not masquerade as a unicast-only object.
        self.assertIsNone(self.get(f".1.3.6.1.2.1.2.2.1.11.{index}")[3])

    def test_bulk_walk_is_ordered_and_contains_counter64(self):
        output = self.successful_client("snmpbulkwalk", ".1.3.6.1")
        records = [manager.parse_snmp_line(line) for line in output.splitlines()]
        # A final GETBULK response may repeat endOfMibView for the remaining
        # repetitions. Those exception bindings are not measurement rows.
        records = [record for record in records if record[1]]
        oids = [tuple(map(int, record[0].lstrip(".").split("."))) for record in records]
        self.assertEqual(oids, sorted(set(oids)))
        self.assertTrue(any(record[1] == "Counter64" for record in records))
        self.assertTrue(any(record[0].startswith(".1.3.6.1.2.1.25.3.3.1.2.") for record in records))

    def test_extended_device_metrics_have_shared_names_and_real_values(self):
        output = self.successful_client("snmpbulkwalk", mib.oid_string(mib.EXAMPLE_ROOT + (4,)))
        records = [manager.parse_snmp_line(line) for line in output.splitlines()]
        records = [record for record in records if record[1]]
        self.assertTrue(records)
        for record in records:
            self.assertIsNotNone(mib.get_mib_object(record[0]), record[0])
        self.assertTrue(any(record[0].startswith(mib.oid_string(mib.FILESYSTEM)) for record in records))
        self.assertTrue(any(record[0].startswith(mib.oid_string(mib.PROCESSES)) for record in records))
        process = self.get(mib.oid_string(mib.PROCESSES + (1, os.getpid())))
        self.assertEqual(process[3], os.getpid())
        self.assertGreater(self.get(mib.oid_string(mib.MEMORY + (1, 0)))[3], 0)
        self.assertEqual(self.get(mib.oid_string(mib.CPU + (1, 0)))[3], psutil.cpu_count(logical=True))

    def test_manager_browse_displays_filesystem_names_units_and_csv(self):
        output = self.agent.path / "filesystems.csv"
        result = run_executable(
            "snmp_data_collector_v12.py", "browse", "--target", self.agent.target,
            "--community", READ_COMMUNITY, "--group", "storage", "--output", output,
            cwd=self.agent.path,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Filesystem capacity", result.stdout)
        self.assertIn("GiB", result.stdout)
        with output.open(newline="", encoding="utf-8") as source:
            self.assertTrue(list(csv.DictReader(source)))

    def test_v1_full_walk_skips_counter64(self):
        output = self.successful_client("snmpwalk", ".1.3.6.1", version="1")
        self.assertNotIn("Counter64", output)
        self.assertIn(".1.3.6.1.4.1.32473.1.2.1.", output)

    def test_missing_object_instance_and_end_of_tree(self):
        missing_object = self.get(".1.3.6.1.4.1.32473.99.0")
        missing_instance = self.get(".1.3.6.1.2.1.1.1.7")
        self.assertIn("No Such Object", missing_object[2])
        self.assertIn("No Such Instance", missing_instance[2])
        if shutil.which("snmpgetnext"):
            output = self.successful_client("snmpgetnext", ".1.3.6.1.4.1.999999.0")
            self.assertIn("No more variables", output)

    def test_read_community_cannot_write(self):
        before = self.get(".1.3.6.1.2.1.1.6.0")[2]
        for version in ("1", "2c"):
            with self.subTest(version=version):
                result = run_client(self.agent, "snmpset", ".1.3.6.1.2.1.1.6.0", "s", "Rejected",
                                    version=version)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.get(".1.3.6.1.2.1.1.6.0")[2], before)

    def test_read_only_default_rejects_set(self):
        with RunningAgent(write_community=None) as agent:
            result = run_client(agent, "snmpset", ".1.3.6.1.2.1.1.5.0", "s", "Rejected")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("notWritable", result.stderr)

    def test_set_validation_is_atomic(self):
        contact = self.get(".1.3.6.1.2.1.1.4.0")[2]
        result = run_client(
            self.agent, "snmpset", ".1.3.6.1.2.1.1.4.0", "s", "Must not commit",
            ".1.3.6.1.2.1.1.6.0", "i", "123", community=WRITE_COMMUNITY,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrongType", result.stderr)
        self.assertEqual(self.get(".1.3.6.1.2.1.1.4.0")[2], contact)
        for data_type, value, expected in (("s", "x" * 256, "wrongLength"), ("x", "ff", "wrongEncoding")):
            with self.subTest(error=expected):
                result = run_client(self.agent, "snmpset", ".1.3.6.1.2.1.1.6.0", data_type, value,
                                    community=WRITE_COMMUNITY)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
        result = run_client(self.agent, "snmpset", ".1.3.6.1.2.1.1.3.0", "t", "0",
                            community=WRITE_COMMUNITY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("notWritable", result.stderr)

    def test_set_persists_across_process_restart(self):
        for version in ("1", "2c"):
            with self.subTest(version=version):
                manager.snmp_set(self.agent.target, version, WRITE_COMMUNITY,
                                 ".1.3.6.1.2.1.1.6.0", "s", "Persistent lab")
                self.assertEqual(self.get(".1.3.6.1.2.1.1.6.0")[2].strip('"'), "Persistent lab")
        saved = json.loads(self.agent.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["sys_location"], "Persistent lab")
        self.agent.stop()
        self.assertEqual(self.agent.process.returncode, 0, self.agent.output)
        self.agent.start()
        self.assertEqual(self.get(".1.3.6.1.2.1.1.6.0")[2].strip('"'), "Persistent lab")

    def test_persistence_failure_does_not_publish_set(self):
        before = self.get(".1.3.6.1.2.1.1.6.0")[2]
        path = self.agent.state_file
        saved = path.read_bytes() if path.exists() else None
        if path.exists():
            path.unlink()
        path.mkdir()
        try:
            result = run_client(self.agent, "snmpset", ".1.3.6.1.2.1.1.6.0", "s", "Must not commit",
                                community=WRITE_COMMUNITY)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("commitFailed", result.stderr)
            self.assertEqual(self.get(".1.3.6.1.2.1.1.6.0")[2], before)
        finally:
            path.rmdir()
            if saved is not None:
                path.write_bytes(saved)

    def test_wrong_community_times_out(self):
        result = run_client(self.agent, "snmpget", ".1.3.6.1.2.1.1.1.0", community="incorrect")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timeout", result.stderr)

    def test_manager_source_address_filter(self):
        request = v2c.GetRequestPDU()
        v2c.apiPDU.set_defaults(request)
        v2c.apiPDU.set_varbinds(request, [(rfc1902.ObjectIdentifier("1.3.6.1.2.1.1.1.0"), rfc1902.Null(""))])
        message = v2c.Message()
        v2c.apiMessage.set_defaults(message)
        v2c.apiMessage.set_community(message, READ_COMMUNITY)
        v2c.apiMessage.set_pdu(message, request)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            try:
                client.bind(("127.0.0.2", 0))
            except OSError:
                self.skipTest("A second loopback source address is unavailable")
            client.settimeout(0.5)
            client.sendto(encoder.encode(message), ("127.0.0.1", self.agent.port))
            with self.assertRaises(socket.timeout):
                client.recvfrom(65535)
        self.assertIn("Python SNMP host agent", self.get(".1.3.6.1.2.1.1.1.0")[2])

    def test_port_conflict_is_reported(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "snmp_agent.py"), "--config", str(self.agent.config_file)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Agent error", result.stderr)
        self.assertIn("Python SNMP host agent", self.get(".1.3.6.1.2.1.1.1.0")[2])

    def test_live_counter_capture_and_comparison_loading(self):
        interfaces = manager.snmp_walk(self.agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
        loopback = next((record for record in interfaces if record[2].strip('"') in ("lo", "lo0")), None)
        if loopback is None:
            self.skipTest("An exposed loopback interface is required for deterministic traffic")
        oid = ".1.3.6.1.2.1.2.2.1.10." + loopback[0].split(".")[-1]
        stop = threading.Event()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
            sink.bind(("127.0.0.1", 0))

            def generate_traffic():
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    while not stop.is_set():
                        sender.sendto(b"x" * 1024, sink.getsockname())
                        stop.wait(0.01)

            worker = threading.Thread(target=generate_traffic)
            worker.start()
            path = self.agent.path / "traffic.csv"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    manager.continuous_capture(self.agent.target, "2c", READ_COMMUNITY, oid,
                                               0.2, path, duration=0.8)
            finally:
                stop.set()
                worker.join(timeout=2)
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(any(row["status"] == "updated" for row in rows))
        self.assertTrue(any(float(row["rate"]) > 0 for row in rows if row["rate"]))
        intervals = path.with_name(path.stem + "_intervals.csv")
        for filename in (path, intervals):
            series = manager.load_comparison_series(filename)
            self.assertEqual(series["unit"], "Mbps")
            self.assertEqual(series["x"][0], 0)
            self.assertTrue(all(value > 0 for value in series["y"]))

    def test_cpu_and_ram_capture_csvs_work_with_manager(self):
        for name, oid in (("cpu", manager.CPU_LOAD_ROOT_OID), ("ram", manager.PHYSICAL_MEMORY_ROOT_OID)):
            with self.subTest(metric=name):
                path = self.agent.path / f"{name}.csv"
                with contextlib.redirect_stdout(io.StringIO()):
                    manager.continuous_capture(self.agent.target, "2c", READ_COMMUNITY, oid,
                                               0.2, path, duration=0.4)
                with path.open(newline="", encoding="utf-8") as source:
                    rows = list(csv.DictReader(source))
                self.assertTrue(rows)
                if name == "cpu":
                    self.assertEqual(int(rows[0]["cpu_core_count"]), psutil.cpu_count(logical=True))
                    self.assertTrue(json.loads(rows[0]["cpu_core_values"]))
                else:
                    self.assertGreater(float(rows[0]["memory_total_gib"]), 0)
                    self.assertGreaterEqual(float(rows[0]["memory_usage_percent"]), 0)
                series = manager.load_comparison_series(path)
                self.assertTrue(series["y"])
                self.assertIn(series["unit"], ("%", "GiB"))


class ConfigurationTests(unittest.TestCase):
    def test_ipv4_network_access(self):
        config = snmp_agent.AgentConfig(allowed_managers=("192.168.20.0/24", "127.0.0.1"))
        self.assertTrue(config.allows_manager("192.168.20.15"))
        self.assertTrue(config.allows_manager("127.0.0.1"))
        self.assertFalse(config.allows_manager("192.168.21.15"))
        self.assertFalse(config.allows_manager("invalid"))

    def test_invalid_settings_are_rejected(self):
        for settings in (
            {"port": 0}, {"port": True}, {"allowed_managers": ()},
            {"allowed_managers": ("::1",)}, {"read_community": ""},
            {"write_community": "public"}, {"sample_interval": float("nan")},
            {"sample_interval": -1}, {"sys_location": "x" * 256},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                snmp_agent.AgentConfig(**settings)


if __name__ == "__main__":
    unittest.main()
