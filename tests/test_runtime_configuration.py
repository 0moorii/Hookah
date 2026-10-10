"""Executable Agent/Manager interoperability and live local/remote configuration."""

import csv
import json
import os
import shutil
import subprocess
import time
import unittest

import snmp_data_collector_v12 as manager
from snmp_protocol import COLLECTION_CONFIG_OID, collection_metadata_oid, oid_text
from tests.helpers import (
    ROOT, TOOLS, READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent,
    executable_environment, loopback_traffic, run_client, run_executable, wait_for,
)


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "Net-SNMP client tools required")
class RuntimeConfigurationTests(unittest.TestCase):
    integration_test = True

    @staticmethod
    def group_value(agent, group, column):
        return manager.snmp_get(agent.target, "2c", READ_COMMUNITY,
                                oid_text(collection_metadata_oid(group, column)))[3]

    def agent_config_cli(self, agent, action, *arguments, remote=True, community=WRITE_COMMUNITY):
        options = ["--remote", agent.target, "--community", community] if remote else ["--config", agent.config_file]
        return run_executable("snmp_agent.py", "config", action, *options, *arguments, cwd=agent.path)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @staticmethod
    def counter_oid(agent):
        records = manager.snmp_walk(agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.2.2.1.2")
        loopback = next((row for row in records if row[2].strip('"') in ("lo", "lo0")), None)
        if loopback is None:
            raise unittest.SkipTest("A named loopback interface is required")
        return ".1.3.6.1.2.1.2.2.1.10." + loopback[0].split(".")[-1]

    def test_both_programs_are_executable_and_manager_cli_reads_agent(self):
        self.assertTrue(os.access(ROOT / "snmp_agent.py", os.X_OK))
        self.assertTrue(os.access(ROOT / "snmp_data_collector_v12.py", os.X_OK))
        with RunningAgent() as agent:
            for version in ("1", "2c"):
                with self.subTest(version=version):
                    result = run_executable(
                        "snmp_data_collector_v12.py", "get", "--target", agent.target,
                        "--community", READ_COMMUNITY, "--version", version,
                        "--oid", ".1.3.6.1.2.1.1.1.0", cwd=agent.path,
                    )
                    self.assert_success(result)
                    self.assertIn("Python SNMP host agent", result.stdout)
            result = run_executable("snmp_data_collector_v12.py", "interactive", cwd=agent.path, input="0\n")
            self.assert_success(result)
            self.assertIn("Goodbye", result.stdout)

    def test_remote_cli_changes_1_10_120_seconds_and_persists_after_restart(self):
        with RunningAgent(sample_interval=120) as agent:
            pid = agent.process.pid
            for seconds in (1, 10, 120):
                with self.subTest(seconds=seconds):
                    result = self.agent_config_cli(agent, "set", "--interval", "ifInOctets", str(seconds))
                    self.assert_success(result)
                    self.assertEqual(json.loads(result.stdout)["collection_intervals"]["ifInOctets"], seconds)
                    self.assertEqual(self.group_value(agent, "ifInOctets", 2), seconds)
                    self.assertEqual(json.loads(agent.config_file.read_text())["collection_intervals"]["ifInOctets"], seconds)
                    self.assertEqual(agent.process.pid, pid)
                    self.assertIsNone(agent.process.poll())
            result = self.agent_config_cli(agent, "show", community=READ_COMMUNITY)
            self.assert_success(result)
            self.assertNotIn("write_community", json.loads(result.stdout))
            agent.stop()
            agent.start()
            self.assertEqual(self.group_value(agent, "ifInOctets", 2), 120)

    def test_local_or_ssh_cli_edit_reloads_while_waiting_120_seconds(self):
        with RunningAgent(sample_interval=120) as agent:
            pid = agent.process.pid
            result = self.agent_config_cli(agent, "set", "--sample-interval", "1", remote=False)
            self.assert_success(result)
            wait_for(lambda: self.group_value(agent, "ifInOctets", 2) == 1, timeout=3)
            self.assertEqual(agent.process.pid, pid)
            result = self.agent_config_cli(agent, "set", "--interval", "ifInOctets", "10", remote=False)
            self.assert_success(result)
            wait_for(lambda: self.group_value(agent, "ifInOctets", 2) == 10)
            result = self.agent_config_cli(agent, "set", "--clear-interval", "ifInOctets", remote=False)
            self.assert_success(result)
            wait_for(lambda: self.group_value(agent, "ifInOctets", 2) == 1)

    def test_queries_return_cached_in_octets_while_other_groups_collect(self):
        with RunningAgent(sample_interval=120, collection_intervals={"ifOutOctets": 0.1}) as agent:
            oid = self.counter_oid(agent)
            before = manager.snmp_get(agent.target, "2c", READ_COMMUNITY, oid)[3]
            incoming_generation = self.group_value(agent, "ifInOctets", 4)
            outgoing_generation = self.group_value(agent, "ifOutOctets", 4)
            with loopback_traffic():
                time.sleep(0.3)
                for _ in range(4):
                    self.assertEqual(manager.snmp_get(agent.target, "2c", READ_COMMUNITY, oid)[3], before)
                self.assertEqual(self.group_value(agent, "ifInOctets", 4), incoming_generation)
                self.assertGreater(self.group_value(agent, "ifOutOctets", 4), outgoing_generation)
                result = self.agent_config_cli(agent, "set", "--interval", "ifInOctets", "0.15")
                self.assert_success(result)
                wait_for(lambda: self.group_value(agent, "ifInOctets", 4) > incoming_generation)
                self.assertGreater(manager.snmp_get(agent.target, "2c", READ_COMMUNITY, oid)[3], before)

    def test_read_community_and_wrong_community_cannot_change_configuration(self):
        with RunningAgent(sample_interval=120) as agent:
            before = agent.config_file.read_bytes()
            for community in (READ_COMMUNITY, "incorrect"):
                with self.subTest(community=community):
                    result = self.agent_config_cli(
                        agent, "set", "--sample-interval", "1", "--timeout", "0.2", community=community,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(agent.config_file.read_bytes(), before)
                    self.assertEqual(self.group_value(agent, "ifInOctets", 2), 120)

    def test_agent_rejects_invalid_wire_updates_and_mixed_sets(self):
        with RunningAgent(sample_interval=120) as agent:
            before = agent.config_file.read_bytes()
            for payload in ('{"sample_interval": 0}', '{"sample_interval": "2m"}',
                            '{"collection_intervals": {"ifInOctets": -1}}', '{"port": 1161}', 'not json'):
                with self.subTest(payload=payload):
                    result = run_client(agent, "snmpset", oid_text(COLLECTION_CONFIG_OID), "s", payload,
                                        community=WRITE_COMMUNITY)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("wrongValue", result.stderr)
                    self.assertEqual(agent.config_file.read_bytes(), before)
            result = run_client(
                agent, "snmpset", oid_text(COLLECTION_CONFIG_OID), "s", '{"sample_interval": 1}',
                ".1.3.6.1.2.1.1.6.0", "s", "Must not commit", community=WRITE_COMMUNITY,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("inconsistentValue", result.stderr)
            self.assertEqual(agent.config_file.read_bytes(), before)
            self.assertEqual(manager.snmp_get(agent.target, "2c", READ_COMMUNITY, ".1.3.6.1.2.1.1.6.0")[2].strip('"'),
                             "Test lab")

    def test_remote_v1_and_v2c_and_clear_override(self):
        with RunningAgent(sample_interval=10) as agent:
            for version in ("1", "2c"):
                with self.subTest(version=version):
                    result = self.agent_config_cli(agent, "set", "--version", version, "--interval", "ifInOctets", "120")
                    self.assert_success(result)
                    self.assertEqual(self.group_value(agent, "ifInOctets", 2), 120)
            result = self.agent_config_cli(agent, "set", "--clear-interval", "ifInOctets")
            self.assert_success(result)
            self.assertEqual(self.group_value(agent, "ifInOctets", 2), 10)

    def test_manager_sync_duration_is_bounded_with_a_120_second_agent(self):
        with RunningAgent(sample_interval=120) as agent:
            output = agent.path / "bounded.csv"
            started = time.monotonic()
            result = run_executable(
                "snmp_data_collector_v12.py", "capture", "--target", agent.target,
                "--community", READ_COMMUNITY, "--oid", self.counter_oid(agent),
                "--sync-agent", "--duration", "0.4", "--output", output,
                cwd=agent.path, timeout=5,
            )
            self.assert_success(result)
            self.assertLess(time.monotonic() - started, 4)
            self.assertIn("120.0 second(s)", result.stdout)
            with output.open(newline="", encoding="utf-8") as source:
                self.assertEqual(len(list(csv.DictReader(source))), 1)

    def test_manager_follows_live_remote_interval_and_saves_synchronized_rates(self):
        with RunningAgent(sample_interval=120) as agent, loopback_traffic():
            output = agent.path / "synchronized.csv"
            process = subprocess.Popen(
                [str(ROOT / "snmp_data_collector_v12.py"), "capture", "--target", agent.target,
                 "--community", READ_COMMUNITY, "--oid", self.counter_oid(agent),
                 "--sync-agent", "--duration", "3", "--output", str(output)],
                env=executable_environment(), cwd=agent.path,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                wait_for(lambda: output.exists() and output.stat().st_size > 0)
                result = self.agent_config_cli(agent, "set", "--interval", "ifInOctets", "0.2")
                self.assert_success(result)
                text, _ = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, text)
                self.assertIn("Agent collection interval changed to 0.2 second(s)", text)
                with output.open(newline="", encoding="utf-8") as source:
                    rows = list(csv.DictReader(source))
                self.assertGreaterEqual(len(rows), 3)
                self.assertTrue(any(row["status"] == "updated" for row in rows))
                self.assertTrue(any(float(row["rate"]) > 0 for row in rows if row["rate"]))
                series = manager.load_comparison_series(output.with_name("synchronized_intervals.csv"))
                self.assertEqual(series["unit"], "Mbps")
                self.assertTrue(series["y"])
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)
