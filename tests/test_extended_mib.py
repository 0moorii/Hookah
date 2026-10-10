"""Expanded MIB collection, shared metadata, human display, and help checks."""

import contextlib
import io
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

import snmp_agent as agent
import snmp_data_collector_v12 as manager
import snmp_mib as mib
from snmp_protocol import COLLECTION_GROUPS, collection_group_for_oid
from tests import test_agent_unit as agent_tests


class ExtendedCollectorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = agent_tests.CollectionTests(methodName="test_get_requests_do_not_trigger_host_collection")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.metrics = self.fixture.metrics

    def test_storage_filesystems_keep_ram_index_and_preserve_large_gauges(self):
        partition = SimpleNamespace(device="C:\\", mountpoint="C:\\", fstype="NTFS", opts="rw,fixed")
        usage = SimpleNamespace(total=8 * 1024 ** 4, used=2 * 1024 ** 4, free=6 * 1024 ** 4, percent=25)
        with mock.patch.object(agent.psutil, "disk_partitions", return_value=[partition]), \
                mock.patch.object(agent.psutil, "disk_usage", return_value=usage):
            values = self.metrics._collect_storage()
        self.assertEqual(int(values[mib.STORAGE + (1, 3)]), 3)
        self.assertEqual(values[mib.STORAGE + (3, 3)].prettyPrint(), "C:\\")
        self.assertEqual(int(values[mib.FILESYSTEM + (10, 1)]), 3)
        self.assertEqual(int(values[mib.FILESYSTEM + (6, 1)].asOctets()), usage.total)
        self.assertEqual(collection_group_for_oid(mib.oid_string(mib.STORAGE + (6, 1))), "memory")
        self.assertEqual(collection_group_for_oid(mib.oid_string(mib.STORAGE + (6, 3))), "storage")
        spec = manager.metric_spec_for_oid(mib.oid_string(mib.FILESYSTEM + (6, 1)), "STRING")
        self.assertFalse(manager.is_counter_kind(spec["kind"]))
        self.assertEqual(manager.transform_sample_value(spec, usage.total), 8192)

    def test_diskio_32_and_64_bit_counters_have_correct_rates(self):
        counters = SimpleNamespace(read_bytes=(1 << 32) + 100, write_bytes=200,
                                   read_count=10, write_count=20, read_time=30, write_time=40,
                                   busy_time=50, read_merged_count=2, write_merged_count=3)
        with mock.patch.object(agent.psutil, "disk_io_counters", return_value={"PhysicalDrive0": counters}):
            values = self.metrics._collect_diskio()
        self.assertEqual(int(values[mib.UCD_DISKIO + (3, 1)]), 100)
        self.assertEqual(int(values[mib.UCD_DISKIO + (9, 1)]), counters.read_bytes)
        spec = manager.metric_spec_for_oid(mib.oid_string(mib.DISKIO + (3, 1)), "Counter64")
        self.assertEqual(manager.counter_rate(spec, 2 * 1024 ** 2, 2), 1)
        self.assertEqual(spec["unit"], "MiB/s")
        self.assertEqual(spec["counter_bits"], 64)
        self.assertEqual(collection_group_for_oid(mib.oid_string(mib.DISKIO + (3, 1))), "diskio")

    def test_processes_publish_real_fields_and_skip_inaccessible_fields(self):
        process = SimpleNamespace(info={"pid": 123, "name": "worker", "exe": "C:\\worker.exe", "username": "user",
                                        "status": "running", "memory_info": SimpleNamespace(rss=4 * 1024 ** 3, vms=8 * 1024 ** 3),
                                        "num_threads": 5, "ppid": 100, "create_time": 1700000000,
                                        "cpu_times": SimpleNamespace(user=2.5, system=1.5)},
                                  cpu_percent=mock.Mock(return_value=150.5))
        denied = SimpleNamespace(info={"pid": 124, "name": "protected", "memory_info": None},
                                 cpu_percent=mock.Mock(side_effect=agent.psutil.AccessDenied(124)))
        with mock.patch.object(agent.psutil, "process_iter", return_value=[process, denied]):
            values = self.metrics._collect_processes()
        self.assertEqual(int(values[mib.PROCESS_PERF + (1, 123)]), 400)
        self.assertEqual(int(values[mib.PROCESS_PERF + (2, 123)]), 4 * 1024 ** 2)
        self.assertEqual(float(values[mib.PROCESSES + (7, 123)].asOctets()), 150.5)
        self.assertNotIn(mib.PROCESSES + (8, 124), values)
        self.assertNotIn(mib.PROCESSES + (7, 124), values)
        self.assertEqual(manager.metric_spec_for_oid(mib.oid_string(mib.PROCESSES + (7, 123)))["kind"], "gauge")

    def test_network_addresses_mac_and_tcp_summary(self):
        ipv4 = SimpleNamespace(family=socket.AF_INET, address="192.168.1.20", netmask="255.255.255.0", broadcast="192.168.1.255", ptp=None)
        ipv6 = SimpleNamespace(family=socket.AF_INET6, address="fe80::1%Ethernet", netmask="ffff:ffff:ffff:ffff::", broadcast=None, ptp=None)
        mac = SimpleNamespace(family=agent.psutil.AF_LINK, address="01-23-45-67-89-ab")
        values = {}
        self.metrics._collect_addresses(values, "Ethernet", 2, [ipv4, ipv6, mac])
        self.assertEqual(values[mib.IF_TABLE + (6, 2)].asOctets(), bytes.fromhex("0123456789ab"))
        self.assertEqual(int(values[mib.IP_ADDRESS_TABLE + (2, 192, 168, 1, 20)]), 2)
        self.assertEqual(values[mib.ADDRESSES + (4, 2)].prettyPrint(), "fe80::1%Ethernet")
        connections = [SimpleNamespace(type=socket.SOCK_STREAM, status=status) for status in ("ESTABLISHED", "CLOSE_WAIT", "LISTEN")]
        connections.append(SimpleNamespace(type=socket.SOCK_DGRAM, status="NONE"))
        with mock.patch.object(agent.psutil, "net_connections", return_value=connections):
            values = self.metrics._collect_connections()
        self.assertEqual(int(values[(1, 3, 6, 1, 2, 1, 6, 9, 0)]), 2)
        self.assertEqual(int(values[mib.CONNECTIONS + (7, 0)]), 1)

    def test_swap_and_optional_temperature_fan_battery_data(self):
        swap = SimpleNamespace(total=8 * 1024 ** 3, used=2 * 1024 ** 3, free=6 * 1024 ** 3, percent=25)
        battery = SimpleNamespace(percent=75.5, secsleft=-2, power_plugged=True)
        temperature = SimpleNamespace(label="CPU", current=42.5, high=80, critical=None)
        fan = SimpleNamespace(label="Case", current=1200)
        with mock.patch.object(agent.psutil, "swap_memory", return_value=swap):
            memory = self.metrics._collect_memory()
        self.assertEqual(memory[mib.STORAGE + (3, 2)].prettyPrint(), "Swap Memory")
        self.assertEqual(int(memory[mib.MEMORY + (10, 0)].asOctets()), swap.total)
        with mock.patch.object(agent.psutil, "sensors_battery", return_value=battery), \
                mock.patch.object(agent.psutil, "sensors_temperatures", return_value={"chip": [temperature]}), \
                mock.patch.object(agent.psutil, "sensors_fans", return_value={"chip": [fan]}):
            values = self.metrics._collect_sensors()
        self.assertEqual(float(values[mib.SENSORS + (1, 0)].asOctets()), 75.5)
        self.assertEqual(float(values[mib.SENSORS + (4, 4, 1)].asOctets()), 42.5)
        self.assertNotIn(mib.SENSORS + (4, 6, 1), values)
        self.assertEqual(int(values[mib.SENSORS + (5, 4, 1)]), 1200)

    def test_windows_service_inventory_and_missing_os_apis(self):
        service = SimpleNamespace(as_dict=lambda: {"name": "HookahSnmpAgent", "display_name": "Hookah Agent",
                                                  "status": "running", "start_type": "automatic", "pid": 123,
                                                  "username": "LocalService", "description": "Metrics", "binpath": "C:\\python.exe"})
        with mock.patch.object(agent.psutil, "win_service_iter", return_value=[service]):
            values = self.metrics._collect_services()
        self.assertEqual(values[mib.SERVICES + (2, 1)].prettyPrint(), "HookahSnmpAgent")
        self.assertEqual(int(values[mib.SERVICES + (6, 1)]), 123)
        with mock.patch.object(agent.psutil, "net_connections", side_effect=agent.psutil.AccessDenied()):
            self.assertEqual(self.metrics._collect_connections(), {})
        with mock.patch.object(agent.psutil, "win_service_iter", side_effect=NotImplementedError):
            self.assertEqual(self.metrics._collect_services(), {})

    def test_every_published_fixture_oid_has_shared_metadata(self):
        for oid in self.metrics.snapshot:
            with self.subTest(oid=oid):
                self.assertIsNotNone(mib.get_mib_object(mib.oid_string(oid)))


class MibAndHelpTests(unittest.TestCase):
    def test_tree_registry_and_catalog_are_consistent_and_extended(self):
        self.assertGreaterEqual(len(mib.OBJECTS), 180)
        self.assertGreaterEqual(len(manager.COMMON_OIDS), 175)
        self.assertEqual(len({entry.oid for entry in mib.OBJECTS}), len(mib.OBJECTS))
        self.assertEqual(len({item["number"] for item in manager.COMMON_OIDS}), len(manager.COMMON_OIDS))
        tree = mib.render_mib_tree()
        for name in ("hostResources", "diskIOEntry", "processEntry", "networkAddresses", "temperatureEntry", "serviceEntry"):
            self.assertIn(name, tree)
        self.assertNotIn("sysDescr.{index}", tree)
        self.assertIn("sysDescr.0", tree)
        self.assertIn("filesystemEntry", mib.render_mib_tree("storage"))
        self.assertNotIn("processEntry", mib.render_mib_tree("storage"))
        self.assertEqual(COLLECTION_GROUPS["system"], 1)
        self.assertEqual(COLLECTION_GROUPS["load"], 7)

    def test_descriptive_help_and_static_tree_do_not_contact_devices(self):
        for arguments in (["help"], ["help", "capture"], ["help", "keys", "generate"], ["mib", "--group", "storage"]):
            output = io.StringIO()
            with mock.patch.object(manager, "run_command") as request, contextlib.redirect_stdout(output):
                self.assertEqual(manager.main(arguments), 0)
                request.assert_not_called()
            self.assertTrue(output.getvalue())
        output = io.StringIO()
        with mock.patch.object(agent, "HostMetrics") as collect, contextlib.redirect_stdout(output):
            self.assertEqual(agent.main(["help", "config", "set"]), 0)
            self.assertIn("seconds", output.getvalue())
            self.assertEqual(agent.main(["mib", "--group", "storage"]), 0)
            collect.assert_not_called()

    def test_invalid_help_topic_and_mib_group_are_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            manager.main(["help", "nonexistent"])
        with self.assertRaises(ValueError):
            mib.render_mib_tree("nonexistent")

    def test_browse_uses_shared_names_and_does_not_plot_text_as_numbers(self):
        oid = mib.oid_string(mib.FILESYSTEM + (6, 1))
        records = [(oid, "STRING", '"1073741824"', 1073741824)]
        output = io.StringIO()
        with mock.patch.object(manager, "snmp_walk", return_value=records), contextlib.redirect_stdout(output):
            values = manager.browse_device("device", group="storage", root=mib.oid_string(mib.FILESYSTEM))
        self.assertEqual(values, records)
        self.assertIn("1.000 GiB", output.getvalue())
        self.assertIn("Filesystem capacity", output.getvalue())
        self.assertIsNone(manager.transform_sample_value({"kind": "text"}, 123))
