"""Manager parsing, measurements, CSV/plots, and scheduling without a network."""

import contextlib
import csv
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snmp_data_collector_v12 as manager
from snmp_protocol import collection_group_for_oid, collection_metadata_oid, oid_text


IN_OID = ".1.3.6.1.2.1.2.2.1.10.1"
HC_IN_OID = ".1.3.6.1.2.1.31.1.1.1.6.1"


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ParsingTests(unittest.TestCase):
    def test_snmp_types_and_counter64_precision(self):
        cases = (
            ("Counter32: 4294967295", 4294967295),
            ("Counter64: 18446744073709551615", (1 << 64) - 1),
            ("INTEGER: up(1)", 1),
            ("INTEGER: -5", -5),
            ("Gauge32: 7", 7),
            ("Timeticks: (12345) 0:02:03.45", 12345),
            ('STRING: "0.42"', 0.42),
            ('STRING: "1e-06"', 0.000001),
            ('STRING: "device name"', None),
            ("OID: .1.3.6.1.4.1.32473.1.1", None),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                result = manager.parse_snmp_line(f"{IN_OID} = {text}")
                self.assertEqual(result[0], IN_OID)
                self.assertEqual(result[3], expected)
        self.assertEqual(manager.parse_snmp_line(""), ("", "", "", None))

    def test_legacy_counter_values_remain_exact(self):
        for text in ("18446744073709551615", "18446744073709551615.0"):
            self.assertEqual(manager.parse_counter_value(text), (1 << 64) - 1)
        for text in ("nan", "inf", "1.5", "not a number"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                manager.parse_counter_value(text)

    def test_command_errors_become_runtime_errors(self):
        with mock.patch.object(manager.subprocess, "run", side_effect=subprocess.TimeoutExpired("snmpget", 15)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                manager.run_command(["snmpget"])
        with mock.patch.object(manager.subprocess, "run", return_value=SimpleResult(1, "", "Timeout")):
            with self.assertRaisesRegex(RuntimeError, "Timeout"):
                manager.run_command(["snmpget"])


class SimpleResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class MeasurementTests(unittest.TestCase):
    def test_counter_wrap_for_both_widths(self):
        for bits in (32, 64):
            with self.subTest(bits=bits):
                self.assertEqual(manager.calculate_counter_delta((1 << bits) - 5, 4, bits), 9)
        self.assertIsNone(manager.calculate_counter_delta(None, 4, 32))

    def test_throughput_packet_rates_and_unit_conversions(self):
        metric = manager.metric_spec_for_oid(IN_OID, "Counter32")
        self.assertEqual(manager.counter_rate(metric, 1_000_000, 1), 8)
        packets = manager.metric_spec_for_oid(".1.3.6.1.2.1.2.2.1.11.1", "Counter32")
        self.assertEqual(manager.counter_rate(packets, 120, 2), 60)
        for kind, raw, expected in (("memory_kib", 1024, 1), ("timeticks", 360000, 1),
                                    ("speed_bps", 100_000_000, 100), ("percent", 25, 25)):
            with self.subTest(kind=kind):
                self.assertEqual(manager.transform_sample_value({"kind": kind}, raw), expected)
        self.assertIsNone(manager.counter_rate(metric, 100, 0))

    def test_high_capacity_octets_have_a_64_bit_mbps_spec(self):
        delta, rate, metric, bits = manager.recalculate_interval_measurement(
            (1 << 64) - 5, 4, 1, HC_IN_OID,
        )
        self.assertEqual(bits, 64)
        self.assertEqual(delta, 9)
        self.assertEqual(metric["unit"], "Mbps")
        self.assertAlmostEqual(rate, 0.000072)

    def test_cpu_average_ignores_invalid_processor_rows(self):
        root = manager.CPU_LOAD_ROOT_OID
        records = [(root + ".1", "INTEGER", "20", 20), (root + ".2", "INTEGER", "50", 50),
                   (root + ".3", "INTEGER", "150", 150), (root + ".4", "STRING", "missing", None)]
        with mock.patch.object(manager, "snmp_walk", return_value=records):
            average, cores, minimum, maximum = manager.snmp_cpu_average("device", "2c", "public")
        self.assertEqual((average, len(cores), minimum, maximum), (35, 2, 20, 50))
        with mock.patch.object(manager, "snmp_walk", return_value=[]), self.assertRaises(RuntimeError):
            manager.snmp_cpu_average("device", "2c", "public")

    def test_memory_discovery_units_and_percentages(self):
        description = manager.HR_STORAGE_DESCR_ROOT + ".8"
        values = {
            manager.HR_STORAGE_TYPE_ROOT + ".8": ("OID", "." + manager.HR_STORAGE_RAM_TYPE, None),
            description: ("STRING", '"Physical Memory"', None),
            manager.HR_STORAGE_ALLOC_ROOT + ".8": ("INTEGER", "4096", 4096),
            manager.HR_STORAGE_SIZE_ROOT + ".8": ("INTEGER", "262144", 262144),
            manager.HR_STORAGE_USED_ROOT + ".8": ("INTEGER", "65536", 65536),
        }

        def get(target, version, community, oid):
            return (oid, *values[oid])

        with mock.patch.object(manager, "snmp_walk", return_value=[(description, *values[description])]), \
                mock.patch.object(manager, "snmp_get", side_effect=get):
            snapshot = manager.get_physical_memory_snapshot("device", "2c", "public")
        self.assertEqual(snapshot["index"], 8)
        self.assertEqual((snapshot["total_gib"], snapshot["used_gib"], snapshot["free_gib"]), (1, 0.25, 0.75))
        self.assertEqual(snapshot["usage_percent"], 25)

    def test_memory_discovery_falls_back_to_storage_type(self):
        with mock.patch.object(manager, "snmp_walk", side_effect=[
            [(manager.HR_STORAGE_DESCR_ROOT + ".8", "STRING", "Main storage", None)],
            [(manager.HR_STORAGE_TYPE_ROOT + ".8", "OID", "." + manager.HR_STORAGE_RAM_TYPE, None)],
        ]):
            index, _ = manager.discover_physical_memory_index("device", "2c", "public")
        self.assertEqual(index, 8)

    def test_sync_metadata_identifies_the_correct_group(self):
        self.assertEqual(collection_group_for_oid(IN_OID), "ifInOctets")
        self.assertEqual(collection_group_for_oid(HC_IN_OID), "ifInOctets")
        with mock.patch.object(manager, "snmp_get", return_value=("meta", "STRING", '"120"', 120)) as get:
            self.assertEqual(manager.snmp_collection_interval("device", "2c", "public", IN_OID), 120)
            get.assert_called_once_with("device", "2c", "public", oid_text(collection_metadata_oid("ifInOctets", 2)))
        with mock.patch.object(manager, "snmp_get", return_value=("meta", "", "No Such Object", None)):
            with self.assertRaises(RuntimeError):
                manager.snmp_collection_interval("device", "2c", "public", IN_OID)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="snmp-manager-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture.csv"
        self.clock = FakeClock()

    def capture(self, responses, interval=1, duration=3, sync=False, schedule=None):
        with mock.patch.object(manager.time, "monotonic", self.clock.monotonic), \
                mock.patch.object(manager.time, "sleep", self.clock.sleep), \
                mock.patch.object(manager, "snmp_get", side_effect=responses) as get, \
                mock.patch.object(manager, "snmp_collection_interval", side_effect=schedule) as metadata, \
                contextlib.redirect_stdout(io.StringIO()):
            manager.continuous_capture("device", "2c", "public", IN_OID, interval, self.path,
                                       duration=duration, sync_agent=sync)
        return get, metadata

    @staticmethod
    def record(value):
        return IN_OID, "Counter32", str(value), value

    def rows(self, path=None):
        with (path or self.path).open(newline="", encoding="utf-8") as source:
            return list(csv.DictReader(source))

    def test_unchanged_counters_use_the_actual_change_window(self):
        self.capture([self.record(100), self.record(100), self.record(200)])
        rows = self.rows()
        self.assertEqual([row["status"] for row in rows], ["initial", "unchanged", "updated"])
        intervals = self.rows(self.path.with_name("capture_intervals.csv"))
        self.assertEqual(len(intervals), 1)
        self.assertEqual(float(intervals[0]["interval_seconds"]), 2)
        self.assertAlmostEqual(float(intervals[0]["rate"]), 0.0004)

    def test_failed_poll_recovers_without_a_false_baseline(self):
        self.capture([self.record(100), RuntimeError("timeout"), self.record(150)])
        rows = self.rows()
        self.assertEqual([float(row["elapsed_seconds"]) for row in rows], [0, 2])
        self.assertEqual(float(rows[-1]["update_interval_seconds"]), 2)
        self.assertEqual(int(rows[-1]["delta"]), 50)

    def test_duration_does_not_wait_120_seconds_or_take_a_late_sample(self):
        get, _ = self.capture([self.record(100)], interval=120, duration=2)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(self.clock.sleeps, [2])
        self.assertEqual(self.clock.now, 102)

    def test_capture_with_no_successful_samples_is_a_runtime_error(self):
        with self.assertRaisesRegex(RuntimeError, "without a successful sample"):
            self.capture([RuntimeError("timeout")], interval=120, duration=1)
        self.assertFalse(self.path.exists())

    def test_synchronized_capture_adapts_while_waiting(self):
        def schedule(*arguments):
            return 10 if self.clock.now < 102 else 1

        get, metadata = self.capture(
            [self.record(100), self.record(200), self.record(250)], duration=4, sync=True, schedule=schedule,
        )
        self.assertEqual(get.call_count, 3)
        self.assertGreater(metadata.call_count, get.call_count)
        self.assertEqual([float(row["elapsed_seconds"]) for row in self.rows()], [0, 2, 3])
        self.assertTrue(all(wait <= 1 for wait in self.clock.sleeps))

    def test_invalid_intervals_and_durations_are_rejected_before_polling(self):
        with mock.patch.object(manager, "snmp_get") as request:
            for value in (0, -1, float("nan"), float("inf"), True, "2m"):
                with self.subTest(interval=value), self.assertRaises(ValueError):
                    manager.continuous_capture("device", "2c", "public", IN_OID, value, self.path)
                with self.subTest(duration=value), self.assertRaises(ValueError):
                    manager.continuous_capture("device", "2c", "public", IN_OID, 1, self.path, duration=value)
            request.assert_not_called()


class CsvAndPlotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="snmp-plot-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def test_standard_csv_appends_without_duplicate_headers(self):
        path = self.path / "get.csv"
        for value in (10, 20):
            manager.save_standard_result(path, "device", "GET", [(IN_OID, "Counter32", str(value), value)])
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual([int(row["numeric_value"]) for row in rows], [10, 20])

    def test_interval_csv_keeps_counter64_precision_and_rollover(self):
        path = self.path / "intervals.csv"
        row = {field: "" for field in manager.INTERVAL_FIELDS}
        row.update(interval_start_seconds="0", interval_end_seconds="2", interval_seconds="2",
                   oid=HC_IN_OID, metric_kind="counter_octets", metric_name="Throughput", rate_unit="Mbps",
                   counter_start=str((1 << 64) - 5), counter_end="4", rate="0.000036")
        manager.append_rows(path, manager.INTERVAL_FIELDS, [row])
        series = manager.load_comparison_series(path)
        self.assertEqual(series["unit"], "Mbps")
        self.assertEqual(series["x"], [0])
        self.assertAlmostEqual(series["y"][0], 0.000036)

    def cpu_csv(self, name):
        path = self.path / name
        rows = []
        for elapsed, value in ((5, 20), (6, 40)):
            row = {field: "" for field in manager.CAPTURE_FIELDS}
            row.update(elapsed_seconds=str(elapsed), oid=manager.CPU_LOAD_ROOT_OID,
                       value_type="AggregateGauge", numeric_value=str(value), display_value=str(value),
                       metric_kind="cpu_average", metric_name="Average CPU load", metric_unit="%")
            rows.append(row)
        manager.append_rows(path, manager.CAPTURE_FIELDS, rows)
        return path

    def test_comparison_normalizes_time_and_checks_units(self):
        series = manager.load_comparison_series(self.cpu_csv("cpu.csv"))
        self.assertEqual(series["x"], [0, 1])
        self.assertEqual(series["y"], [20, 40])
        self.assertTrue(manager.comparison_compatible(series, {"metric_kind": "percent", "unit": "%"}))
        self.assertFalse(manager.comparison_compatible(series, {"metric_kind": "physical_memory", "unit": "GiB"}))

    def test_headless_plot_creates_a_png(self):
        path = self.cpu_csv("cpu.csv")
        output = self.path / "cpu.png"
        with mock.patch("builtins.input", side_effect=["2", str(output), "n"]), \
                contextlib.redirect_stdout(io.StringIO()):
            manager.plot_csv(path)
        self.assertEqual(output.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(output.stat().st_size, 1000)

    def test_headless_comparison_creates_a_png(self):
        first, second = self.cpu_csv("cpu-a.csv"), self.cpu_csv("cpu-b.csv")
        output = self.path / "comparison.png"
        with mock.patch("builtins.input", side_effect=["A", "B", "1", "2", "CPU comparison", str(output), "n"]), \
                contextlib.redirect_stdout(io.StringIO()):
            manager.plot_two_csv_comparison(first, second)
        self.assertEqual(output.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
