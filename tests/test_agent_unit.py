"""Agent configuration and collection tests with deterministic clocks/host data."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pysnmp.proto import rfc1902
from pysnmp.smi import error

import snmp_agent as agent
from snmp_protocol import COLLECTION_CONFIG_OID, COLLECTION_GROUPS, collection_metadata_oid


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ConfigCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="snmp-config-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.cli("init")

    def cli(self, action, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            result = agent.main(["config", action, "--config", str(self.path), *arguments])
        self.assertEqual(result, 0)
        return json.loads(output.getvalue())

    def test_seconds_values_one_ten_and_two_minutes_persist(self):
        for seconds in (1, 10, 120):
            with self.subTest(seconds=seconds):
                displayed = self.cli("set", "--sample-interval", str(seconds))
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(saved["sample_interval"], seconds)
                self.assertIsInstance(saved["sample_interval"], (int, float))
                self.assertEqual(displayed, self.cli("show"))

    def test_per_metric_updates_merge_and_clear(self):
        self.cli("set", "--interval", "ifInOctets", "10", "--interval", "ifOutOctets", "1")
        saved = self.cli("set", "--interval", "ifInOctets", "120")
        self.assertEqual(saved["collection_intervals"], {"ifInOctets": 120, "ifOutOctets": 1})
        saved = self.cli("set", "--clear-interval", "ifInOctets")
        validated = agent.config_from_document(saved, self.path.parent)
        self.assertEqual(validated.interval_for("ifInOctets"), validated.sample_interval)
        self.assertEqual(validated.interval_for("ifOutOctets"), 1)

    def test_configuration_fields_can_be_changed_without_an_editor(self):
        saved = self.cli("set", "--set", "write_community", "lab-write",
                         "--set", "allowed_managers", '["127.0.0.1/32", "192.168.1.10/32"]',
                         "--set", "sys_location", "Room 2")
        self.assertEqual(saved["write_community"], "lab-write")
        self.assertEqual(saved["sys_location"], "Room 2")
        self.assertIn("192.168.1.10/32", saved["allowed_managers"])

    def test_invalid_intervals_do_not_change_the_file(self):
        before = self.path.read_bytes()
        arguments = [
            ["--sample-interval", value] for value in ("0", "-1", "nan", "inf", "2m", "120s")
        ] + [
            ["--interval", "ifInOctets", value] for value in ("0", "-1", "nan", "inf", "2m", "120s")
        ] + [["--set", "sample_interval", "true"], ["--interval", "unknownMetric", "10"]]
        for change in arguments:
            with self.subTest(change=change), self.assertRaises(SystemExit) as caught:
                self.cli("set", *change)
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(self.path.read_bytes(), before)

    def test_init_does_not_overwrite_an_existing_configuration(self):
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.cli("init")
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_atomic_edit_retains_file_permissions(self):
        self.path.chmod(0o640)
        self.cli("set", "--sample-interval", "120")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_remote_patch_is_rejected_before_network_access(self):
        with mock.patch.object(agent, "remote_configuration") as request:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                agent.main(["config", "set", "--remote", "127.0.0.1:1161", "--community", "lab-write",
                            "--interval", "ifInOctets", "2m"])
            request.assert_not_called()

    def test_relative_state_path_is_resolved_next_to_configuration(self):
        saved = self.cli("set", "--set", "state_file", "state/system.json")
        self.assertEqual(saved["state_file"], "state/system.json")
        config = agent.config_from_document(saved, self.path.parent)
        self.assertEqual(config.state_file, self.path.parent / "state" / "system.json")


class SchedulerTests(unittest.TestCase):
    def test_independent_one_ten_and_120_second_schedules(self):
        schedule = agent.CollectionScheduler(agent.AgentConfig(
            sample_interval=10, collection_intervals={"ifInOctets": 120, "ifOutOctets": 1},
        ))
        self.assertEqual(schedule.due(0), set(COLLECTION_GROUPS))
        schedule.collected(COLLECTION_GROUPS, 0)
        self.assertEqual(schedule.due(0.999), set())
        self.assertEqual(schedule.due(1), {"ifOutOctets"})
        schedule.collected({"ifOutOctets"}, 1)
        self.assertNotIn("ifInOctets", schedule.due(10))
        schedule.collected(schedule.due(10), 10)
        self.assertNotIn("ifInOctets", schedule.due(119.999))
        self.assertIn("ifInOctets", schedule.due(120))

    def test_shorter_and_longer_intervals_are_relative_to_last_collection(self):
        schedule = agent.CollectionScheduler(agent.AgentConfig(sample_interval=120))
        schedule.collected(COLLECTION_GROUPS, 0)
        schedule.config = agent.AgentConfig(sample_interval=1)
        self.assertEqual(schedule.due(2), set(COLLECTION_GROUPS))
        schedule.collected(COLLECTION_GROUPS, 2)
        schedule.config = agent.AgentConfig(sample_interval=120)
        self.assertEqual(schedule.due(121.999), set())
        self.assertEqual(schedule.due(122), set(COLLECTION_GROUPS))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="snmp-collection-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.config = agent.AgentConfig(
            sample_interval=10, collection_intervals={"ifInOctets": 120, "ifOutOctets": 1},
            write_community="unit-write", sys_name="unit-device",
        )
        agent.write_json_atomic(self.path, agent.config_document(self.config))
        self.clock = Clock()
        self.io = SimpleNamespace(bytes_recv=100, bytes_sent=200, packets_recv=3, packets_sent=4,
                                  dropin=0, dropout=0, errin=0, errout=0)
        self.memory = SimpleNamespace(total=16 * 1024 ** 3, available=8 * 1024 ** 3)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.cpu = stack.enter_context(mock.patch.object(agent.psutil, "cpu_percent", return_value=[20, 40]))
        stack.enter_context(mock.patch.object(agent.psutil, "cpu_times_percent", return_value=SimpleNamespace(idle=70)))
        stack.enter_context(mock.patch.object(agent.psutil, "virtual_memory", return_value=self.memory))
        self.network = stack.enter_context(mock.patch.object(agent.psutil, "net_io_counters", return_value={"lo": self.io}))
        stack.enter_context(mock.patch.object(agent.psutil, "net_if_stats", return_value={
            "lo": SimpleNamespace(isup=True, flags="up,loopback,running", speed=0, mtu=65536),
        }))
        stack.enter_context(mock.patch.object(agent.psutil, "net_if_addrs", return_value={"lo": []}))
        stack.enter_context(mock.patch.object(agent.psutil, "boot_time", return_value=0))
        stack.enter_context(mock.patch.object(agent.psutil, "getloadavg", return_value=(0.2, 0.3, 0.4)))
        for name, value in (("users", []), ("pids", [1, 2]), ("cpu_count", None), ("cpu_freq", None),
                            ("cpu_stats", None), ("swap_memory", None), ("disk_partitions", []),
                            ("disk_io_counters", {}), ("process_iter", []), ("net_connections", None),
                            ("sensors_battery", None), ("sensors_temperatures", {}), ("sensors_fans", {}),
                            ("win_service_iter", [])):
            stack.enter_context(mock.patch.object(agent.psutil, name, return_value=value, create=True))
        stack.enter_context(mock.patch.object(agent.socket, "if_nameindex", return_value=[(1, "lo")]))
        self.metrics = agent.HostMetrics(self.config, self.path, clock=self.clock)

    def test_in_octets_are_cached_for_120_seconds_while_out_octets_update(self):
        incoming = agent.IF_TABLE + (10, 1)
        outgoing = agent.IF_TABLE + (16, 1)
        self.io.bytes_recv = 900
        self.io.bytes_sent = 800
        self.clock.now = 1
        self.assertEqual(self.metrics.refresh(), {"ifOutOctets"})
        self.assertEqual(int(self.metrics.snapshot[incoming]), 100)
        self.assertEqual(int(self.metrics.snapshot[outgoing]), 800)
        self.clock.now = 119.999
        self.metrics.refresh()
        self.assertEqual(int(self.metrics.snapshot[incoming]), 100)
        self.clock.now = 120
        self.metrics.refresh()
        self.assertEqual(int(self.metrics.snapshot[incoming]), 900)

    def test_get_requests_do_not_trigger_host_collection(self):
        controller = agent.HostMibController(self.metrics)
        calls = self.network.call_count
        self.io.bytes_recv = 500
        for _ in range(5):
            result = controller.read_variables((rfc1902.ObjectName(agent.IF_TABLE + (10, 1)), rfc1902.Null("")))
            self.assertEqual(int(result[0][1]), 100)
        self.assertEqual(self.network.call_count, calls)

    def test_remote_patch_persists_and_activates_without_collecting_early(self):
        generation = self.metrics.scheduler.sample_counts["ifInOctets"]
        self.metrics.set_collection_configuration({"collection_intervals": {"ifInOctets": 10}})
        self.assertEqual(json.loads(self.path.read_text())["collection_intervals"]["ifInOctets"], 10)
        self.assertEqual(self.metrics.config.interval_for("ifInOctets"), 10)
        self.assertEqual(self.metrics.scheduler.sample_counts["ifInOctets"], generation)
        self.io.bytes_recv = 400
        self.clock.now = 9.999
        self.metrics.refresh()
        self.assertEqual(int(self.metrics.snapshot[agent.IF_TABLE + (10, 1)]), 100)
        self.clock.now = 10
        self.metrics.refresh()
        self.assertEqual(int(self.metrics.snapshot[agent.IF_TABLE + (10, 1)]), 400)

    def test_local_file_change_is_reloaded_and_invalid_file_keeps_previous_settings(self):
        agent.update_config_file(self.path, {"collection_intervals": {"ifInOctets": 1}})
        self.assertTrue(self.metrics.reload_configuration())
        self.assertEqual(self.metrics.config.interval_for("ifInOctets"), 1)
        self.path.write_text('{"sample_interval": -1}', encoding="utf-8")
        with self.assertLogs(agent.LOGGER, level="WARNING"):
            self.assertFalse(self.metrics.reload_configuration())
        self.assertEqual(self.metrics.config.interval_for("ifInOctets"), 1)
        valid = agent.config_document(self.config)
        valid["collection_intervals"]["ifInOctets"] = 2
        agent.write_json_atomic(self.path, valid)
        self.assertTrue(self.metrics.reload_configuration())
        self.assertEqual(self.metrics.config.interval_for("ifInOctets"), 2)

    def test_sample_metadata_matches_published_counter_generation(self):
        self.clock.now = 120
        self.io.bytes_recv = 4321
        self.metrics.refresh()
        self.assertEqual(float(self.metrics.snapshot[collection_metadata_oid("ifInOctets", 3)].asOctets()), 120)
        self.assertEqual(int(self.metrics.snapshot[collection_metadata_oid("ifInOctets", 4)]), 2)
        self.assertEqual(int(self.metrics.snapshot[agent.IF_TABLE + (10, 1)]), 4321)

    def test_counter32_wrap_and_counter64_preservation(self):
        self.io.bytes_recv = (1 << 32) + 9
        self.metrics.refresh(force=True)
        self.assertEqual(int(self.metrics.snapshot[agent.IF_TABLE + (10, 1)]), 9)
        self.assertEqual(int(self.metrics.snapshot[agent.IFX_TABLE + (6, 1)]), (1 << 32) + 9)

    def test_large_memory_uses_valid_allocation_units(self):
        self.memory.total = 16 * 1024 ** 4
        self.memory.available = 8 * 1024 ** 4
        self.metrics.refresh(force=True)
        allocation = int(self.metrics.snapshot[agent.STORAGE + (4, 1)])
        units = int(self.metrics.snapshot[agent.STORAGE + (5, 1)])
        self.assertLessEqual(units, agent.INTEGER_MAX)
        self.assertEqual(allocation * units, self.memory.total)

    def test_config_set_rejects_invalid_and_mixed_transactions_atomically(self):
        controller = agent.HostMibController(self.metrics)
        before = self.path.read_bytes()
        name = rfc1902.ObjectName(COLLECTION_CONFIG_OID)
        with self.assertRaises(error.WrongValueError):
            controller.write_variables((name, rfc1902.OctetString('{"sample_interval": 0}')))
        with self.assertRaises(error.InconsistentValueError):
            controller.write_variables(
                (name, rfc1902.OctetString('{"sample_interval": 1}')),
                (rfc1902.ObjectName(agent.SYSTEM + (6, 0)), rfc1902.OctetString("Changed")),
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.metrics.config.sample_interval, 10)
