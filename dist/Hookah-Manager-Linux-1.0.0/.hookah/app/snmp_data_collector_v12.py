#!/usr/bin/env python3
r"""
  _   _  ___   ___  _  __    _    _   _
 | | | |/ _ \ / _ \| |/ /   / \  | | | |
 | |_| | | | | | | | ' /   / _ \ | |_| |
 |  _  | |_| | |_| | . \  / ___ \|  _  |
 |_| |_|\___/ \___/|_|\_\/_/   \_\_| |_|


                     .  .
                      ..
                    .::::.
                    ::::::
                    '::::'
                 ...  ::  ...
                .::::::::::::.
                      ::
                    .::::.     .....
                  .::'::'::.::::'   ::
                 ::   ::   ::      ::
                 ::...::...::   ...::
                  '::::::::'  .::'
                    :::::::::::'

                      Hookah

 Monitor CPU, memory, and network traffic with SNMP GET/WALK/SET.
 Capture live metrics, export CSVs, and plot or compare sessions.
 Optional RSA-encrypted communication with your local devices.

 Made by 0moorii

SNMP Data Collector and Plotter v12
--------------------------------
Uses Net-SNMP command-line tools:
    snmpget
    snmpwalk
    snmpset

Features:
1. SNMP GET with terminal and/or CSV output
2. SNMP WALK with terminal and/or CSV output
3. SNMP SET
4. Common OID catalog with interface/system/CPU/memory metrics
5. Continuous polling of counters, gauges and status OIDs
6. Automatic rate conversion for octets, packets, errors and discards
7. Metric-aware plotting for throughput, CPU, memory, uptime, status, etc.
8. Adaptive graph scaling and selectable line colors
9. Two-session comparison plotting with two overlaid measurement lines

Designed primarily for SNMPv1/v2c devices on a local lab network.
"""

import argparse
import contextlib
import csv
import importlib.util
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import snmp_mib as mib
from hookah_version import __version__
from snmp_cli_help import show_command_help
from snmp_manager_config import CONFIG_NAME, ManagerConfig, initialize_manager_config, load_manager_config

from snmp_protocol import collection_group_for_oid, collection_metadata_oid, oid_text


HOOKAH_BANNER = (__doc__ or "Hookah").split("SNMP Data Collector and Plotter", 1)[0].strip("\n")
_TRAFFIC_CIPHER = None
_MANAGER_CONFIG = ManagerConfig()


def configure_encryption(path=None):
    """Apply an optional manager encryption profile to all GET/WALK/SET requests."""
    global _TRAFFIC_CIPHER
    if path is None:
        _TRAFFIC_CIPHER = None
    else:
        from snmp_crypto import RsaEnvelope, load_encryption_settings
        _TRAFFIC_CIPHER = RsaEnvelope.from_settings(load_encryption_settings(path))


def print_banner():
    print(HOOKAH_BANNER)
    print()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def now_iso():
    """Return local timestamp with millisecond precision."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def ensure_csv_suffix(filename):
    path = Path(filename).expanduser()
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")
    return path


def python_snmp_backend():
    return os.environ.get("HOOKAH_SNMP_BACKEND") == "python" and _MANAGER_CONFIG.snmp_bin is None


def snmp_tool(command):
    if python_snmp_backend():
        return "bundled PySNMP: " + command if importlib.util.find_spec("pysnmp") is not None else None
    search_path = str(_MANAGER_CONFIG.snmp_bin) if _MANAGER_CONFIG.snmp_bin else None
    return shutil.which(command, path=search_path)


def check_dependencies(commands=("snmpget", "snmpwalk", "snmpset")):
    missing = [command for command in commands if snmp_tool(command) is None]
    if missing:
        print("ERROR: Missing Net-SNMP utilities: " + ", ".join(missing), file=sys.stderr)
        if sys.platform == "win32":
            print("Install the Net-SNMP Windows command-line tools, then add their bin directory to PATH\n"
                  "or set snmp_bin in snmp_manager_config.json (or pass --snmp-bin).", file=sys.stderr)
        else:
            print("Install the clients for your Linux distribution:\n"
                  "  Debian/Ubuntu: sudo apt install snmp\n"
                  "  Fedora/RHEL:   sudo dnf install net-snmp-utils\n"
                  "  Arch Linux:    sudo pacman -S net-snmp\n"
                  "  openSUSE:      sudo zypper install net-snmp", file=sys.stderr)
        return False
    return True


def run_command(args):
    """
    Run a command safely without shell=True.
    Returns stdout text or raises RuntimeError.
    """
    command_args = list(args)
    proxy_context = contextlib.nullcontext()
    if _TRAFFIC_CIPHER is not None:
        from snmp_crypto import EncryptedSnmpProxy
        if len(command_args) < 12:
            raise RuntimeError("Encrypted requests require the common SNMP target arguments.")
        proxy_context = EncryptedSnmpProxy(command_args[11], _TRAFFIC_CIPHER)
    try:
        with proxy_context as proxy:
            if proxy is not None:
                command_args[11] = proxy.snmp_target
            if python_snmp_backend():
                from snmp_python_client import run_client
                try:
                    return run_client(command_args)
                except RuntimeError as exc:
                    if proxy is not None and proxy.last_error:
                        raise RuntimeError(f"Encrypted SNMP transport failed: {proxy.last_error}") from exc
                    raise
            result = subprocess.run(
                command_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(15, _MANAGER_CONFIG.timeout * (_MANAGER_CONFIG.retries + 1) + 5),
                check=False,
            )
            if result.returncode != 0 and proxy is not None and proxy.last_error:
                raise RuntimeError(f"Encrypted SNMP transport failed: {proxy.last_error}")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("SNMP request timed out.") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {args[0]}") from exc

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(error or f"{args[0]} failed.")

    return result.stdout.strip()


def build_common_args(command, version, community, target):
    """
    Build common Net-SNMP command line for SNMPv1/v2c.

    -On  -> print OIDs numerically
    -Oa  -> print printable OCTET STRING values as ASCII where possible
    """
    executable = snmp_tool(command) if _MANAGER_CONFIG.snmp_bin else command
    if executable is None:
        raise RuntimeError(f"Command not found in {_MANAGER_CONFIG.snmp_bin}: {command}")
    return [
        executable,
        "-v", version,
        "-c", community,
        "-On",
        "-Oa",
        "-t", f"{_MANAGER_CONFIG.timeout:g}",
        "-r", str(_MANAGER_CONFIG.retries),
        target,
    ]


def parse_snmp_line(line):
    """
    Parse a typical Net-SNMP line such as:
      .1.3.6.1.2.1.2.2.1.10.5 = Counter32: 123456

    Returns:
      oid, value_type, value, numeric_value
    """
    line = line.strip()

    if not line:
        return "", "", "", None

    if " = " in line:
        oid, raw_value = line.split(" = ", 1)
    else:
        # Fallback for unusual output
        parts = line.split(maxsplit=1)
        oid = parts[0]
        raw_value = parts[1] if len(parts) > 1 else ""

    oid = oid.strip()
    raw_value = raw_value.strip()

    value_type = ""
    value = raw_value

    if ": " in raw_value:
        value_type, value = raw_value.split(": ", 1)
        value_type = value_type.strip()
        value = value.strip()

    numeric_value = extract_numeric_value(value_type, value)
    return oid, value_type, value, numeric_value


def extract_numeric_value(value_type, value):
    """
    Extract a useful numeric value from common SNMP values.

    Examples:
      Counter32: 12345           -> 12345
      INTEGER: up(1)             -> 1
      Gauge32: 7                 -> 7
      Timeticks: (12345) ...     -> 12345
      STRING: "0.42"             -> 0.42
    """
    value = value.strip()

    # Timeticks often contains the raw count in parentheses.
    if value_type.lower().startswith("timeticks"):
        m = re.search(r"\((\d+)\)", value)
        if m:
            return int(m.group(1))

    # Enum-like INTEGER: up(1)
    m = re.search(r"\((-?\d+)\)\s*$", value)
    if m:
        return int(m.group(1))

    # Remove quotes around simple numeric strings, common with UCD-SNMP load OIDs.
    unquoted = value
    if len(unquoted) >= 2 and unquoted[0] == '"' and unquoted[-1] == '"':
        unquoted = unquoted[1:-1].strip()

    # Plain integer or floating point.
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*", unquoted)
    if m:
        token = m.group(1)
        if "." not in token and "e" not in token.lower():
            return int(token)
        number = float(token)
        return int(number) if number.is_integer() else number

    # Try the first standalone numeric token for common numeric SNMP types.
    if any(word in value_type.lower() for word in
           ("counter", "gauge", "integer", "unsigned")):
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value)
        if m:
            token = m.group(0)
            if "." not in token and "e" not in token.lower():
                return int(token)
            number = float(token)
            return int(number) if number.is_integer() else number

    return None


def parse_counter_value(value):
    """Read legacy numeric CSV counters without rounding a Counter64 through float."""
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid counter value.") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("Counter values must be finite integers.")
    return int(number)


def positive_seconds(value):
    if isinstance(value, bool):
        raise ValueError("Intervals must be finite positive numbers in seconds.")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Use numeric seconds, e.g. 1, 10, or 120.") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Intervals must be finite positive numbers in seconds.")
    return seconds

def append_rows(path, fieldnames, rows):
    """Append rows to a CSV file, creating the header when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and path.stat().st_size > 0

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def prompt_connection():
    default_target = _MANAGER_CONFIG.target or ""
    target = input(f"Target IP/hostname [{default_target}]: ").strip() or default_target
    if not target:
        raise ValueError("A target IP/hostname is required.")
    version = input(f"SNMP version [{_MANAGER_CONFIG.version}]: ").strip() or _MANAGER_CONFIG.version

    if version not in ("1", "2c"):
        print("This version of the program supports SNMP v1 and v2c.")
        print("Using SNMP v2c.")
        version = "2c"

    community = input(f"Community string [{_MANAGER_CONFIG.community}]: ").strip() or _MANAGER_CONFIG.community
    return target, version, community


# ---------------------------------------------------------------------------
# Common OID catalog and metric metadata
# ---------------------------------------------------------------------------

COMMON_OIDS = [
    {
        "number": "1",
        "name": "Interface description (ifDescr)",
        "template": ".1.3.6.1.2.1.2.2.1.2.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Shows the interface name/description; useful for finding the correct NIC.",
    },
    {
        "number": "2",
        "name": "Interface operational status (ifOperStatus)",
        "template": ".1.3.6.1.2.1.2.2.1.8.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Current operational state: up(1), down(2), testing(3), etc.",
    },
    {
        "number": "3",
        "name": "Interface administrative status (ifAdminStatus)",
        "template": ".1.3.6.1.2.1.2.2.1.7.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Administrative state of the interface.",
    },
    {
        "number": "4",
        "name": "Incoming octets (ifInOctets)",
        "template": ".1.3.6.1.2.1.2.2.1.10.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "32-bit cumulative received-byte counter; converted to average Mbps.",
    },
    {
        "number": "5",
        "name": "Outgoing octets (ifOutOctets)",
        "template": ".1.3.6.1.2.1.2.2.1.16.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "32-bit cumulative transmitted-byte counter; converted to average Mbps.",
    },
    {
        "number": "6",
        "name": "Incoming unicast packets (ifInUcastPkts)",
        "template": ".1.3.6.1.2.1.2.2.1.11.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative received unicast packets; converted to packets/s.",
    },
    {
        "number": "7",
        "name": "Outgoing unicast packets (ifOutUcastPkts)",
        "template": ".1.3.6.1.2.1.2.2.1.17.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative transmitted unicast packets; converted to packets/s.",
    },
    {
        "number": "8",
        "name": "Incoming errors (ifInErrors)",
        "template": ".1.3.6.1.2.1.2.2.1.14.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative receive errors; converted to errors/s.",
    },
    {
        "number": "9",
        "name": "Outgoing errors (ifOutErrors)",
        "template": ".1.3.6.1.2.1.2.2.1.20.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative transmit errors; converted to errors/s.",
    },
    {
        "number": "10",
        "name": "Incoming discards (ifInDiscards)",
        "template": ".1.3.6.1.2.1.2.2.1.13.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative received packets discarded; converted to discards/s.",
    },
    {
        "number": "11",
        "name": "Outgoing discards (ifOutDiscards)",
        "template": ".1.3.6.1.2.1.2.2.1.19.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Cumulative transmitted packets discarded; converted to discards/s.",
    },
    {
        "number": "12",
        "name": "Interface speed (ifSpeed)",
        "template": ".1.3.6.1.2.1.2.2.1.5.{index}",
        "index_label": "interface index",
        "platform": "Windows/Linux",
        "use": "Reported interface speed; graphed in Mbps.",
    },
    {
        "number": "13",
        "name": "System uptime (sysUpTime)",
        "template": ".1.3.6.1.2.1.1.3.0",
        "platform": "Windows/Linux",
        "use": "SNMP system uptime; graphed in hours.",
    },
    {
        "number": "14",
        "name": "CPU load per logical processor (hrProcessorLoad)",
        "template": ".1.3.6.1.2.1.25.3.3.1.2.{index}",
        "index_label": "processor index",
        "platform": "Windows/Linux if HOST-RESOURCES-MIB is exposed",
        "use": "Processor load percentage for one logical processor.",
    },
    {
        "number": "15",
        "name": "Average CPU load - all logical processors",
        "template": ".1.3.6.1.2.1.25.3.3.1.2",
        "platform": "Windows/Linux if HOST-RESOURCES-MIB is exposed",
        "use": "WALKs every hrProcessorLoad entry and calculates overall average CPU utilization.",
    },
    {
        "number": "16",
        "name": "Total physical memory (hrMemorySize)",
        "template": ".1.3.6.1.2.1.25.2.2.0",
        "platform": "Windows/Linux if HOST-RESOURCES-MIB is exposed",
        "use": "Total physical memory in KiB; graphed as MiB.",
    },
    {
        "number": "17",
        "name": "Linux CPU idle (ssCpuIdle)",
        "template": ".1.3.6.1.4.1.2021.11.11.0",
        "platform": "Linux Net-SNMP",
        "use": "CPU idle percentage; useful alongside CPU load measurements.",
    },
    {
        "number": "18",
        "name": "Linux 1-minute load average",
        "template": ".1.3.6.1.4.1.2021.10.1.3.1",
        "platform": "Linux Net-SNMP",
        "use": "1-minute system load average; often returned as a numeric STRING.",
    },
    {
        "number": "19",
        "name": "Linux available physical memory (memAvailReal)",
        "template": ".1.3.6.1.4.1.2021.4.6.0",
        "platform": "Linux Net-SNMP",
        "use": "Available physical memory in KiB; graphed as MiB.",
    },
    {
        "number": "20",
        "name": "System description / OS information (sysDescr)",
        "template": ".1.3.6.1.2.1.1.1.0",
        "platform": "Windows/Linux",
        "use": "Returns the device/system description, commonly including OS and hardware information.",
    },
    {
        "number": "21",
        "name": "System object identifier (sysObjectID)",
        "template": ".1.3.6.1.2.1.1.2.0",
        "platform": "Windows/Linux",
        "use": "Identifies the vendor/device SNMP implementation.",
    },
    {
        "number": "22",
        "name": "System contact (sysContact)",
        "template": ".1.3.6.1.2.1.1.4.0",
        "platform": "Windows/Linux",
        "use": "Returns the configured administrator/contact information.",
    },
    {
        "number": "23",
        "name": "System name / hostname (sysName)",
        "template": ".1.3.6.1.2.1.1.5.0",
        "platform": "Windows/Linux",
        "use": "Returns the configured hostname/device name.",
    },
    {
        "number": "24",
        "name": "System location (sysLocation)",
        "template": ".1.3.6.1.2.1.1.6.0",
        "platform": "Windows/Linux",
        "use": "Returns the configured physical or logical location of the device.",
    },
    {
        "number": "25",
        "name": "Physical RAM summary / utilization (auto-discover)",
        "template": ".1.3.6.1.2.1.25.2.3",
        "platform": "Windows/Linux if HOST-RESOURCES-MIB storage table is exposed",
        "use": "Automatically finds Physical Memory and returns total, used, free RAM and utilization percentage.",
    },
]


COMMON_OIDS.extend(mib.catalog_extensions(item["template"] for item in COMMON_OIDS))


STATUS_OPER = {
    1: "Up",
    2: "Down",
    3: "Testing",
    4: "Unknown",
    5: "Dormant",
    6: "Not present",
    7: "Lower-layer down",
}

STATUS_ADMIN = {
    1: "Up",
    2: "Down",
    3: "Testing",
}


def normalize_oid(oid):
    return (oid or "").strip().lstrip(".")


def print_oid_reference():
    print("\n" + "=" * 96)
    print(" COMMON SNMP OID REFERENCE")
    print("=" * 96)

    sections = [
        ("Interface / traffic", {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"}),
        ("System information", {"20", "21", "13", "22", "23", "24"}),
        ("CPU / memory", {"14", "15", "16", "17", "18", "19", "25"}),
    ]

    for group in ("storage", "diskio", "processes", "connections", "sensors", "services", "interfaces", "cpu", "memory", "system", "load"):
        numbers = {item["number"] for item in COMMON_OIDS if item.get("section") == group}
        if numbers:
            sections.append((group.title() + " / extended OIDs", numbers))

    for heading, numbers in sections:
        print(f"\n{heading}")
        print("-" * len(heading))
        for item in COMMON_OIDS:
            if item["number"] not in numbers:
                continue
            print(f'{item["number"]:>2}. {item["name"]}')
            print(f'    OID: {item["template"]}')
            print(f'    Use: {item["use"]}')
            print(f'    Platform: {item["platform"]}')

    print("\nUseful discovery WALKs")
    print("----------------------")
    print("Interfaces:")
    print("  .1.3.6.1.2.1.2.2.1.2")
    print("  -> returns ifDescr.<index>, so you can identify Wi-Fi/Ethernet index")
    print("Operational status for all interfaces:")
    print("  .1.3.6.1.2.1.2.2.1.8")
    print("System information:")
    print("  .1.3.6.1.2.1.1")
    print("  -> description/OS, object ID, uptime, contact, hostname and location")
    print("Physical-memory storage entries:")
    print("  .1.3.6.1.2.1.25.2.3.1.3")
    print("  -> hrStorageDescr; look for Physical Memory")
    print("CPU processor-load entries:")
    print("  .1.3.6.1.2.1.25.3.3.1.2")
    print("  -> WALK returns one percentage for each logical processor")
    print("  -> selecting catalog option 15 automatically averages all of them")
    print("=" * 96)


def choose_oid_from_catalog(allow_custom=True):
    """
    Let the user choose a common OID or type a custom OID.
    """
    print_oid_reference()

    if allow_custom:
        print("\n 0. Custom OID")

    choice = input("\nChoose OID number [0 = custom]: ").strip() or "0"

    item = next((x for x in COMMON_OIDS if x["number"] == choice), None)
    if item is None:
        if allow_custom:
            return input("Enter custom OID: ").strip()
        return ""

    oid = item["template"]
    if "{index}" in oid:
        index_value = input(f'Enter {item["index_label"]}: ').strip()
        if not index_value:
            raise ValueError(f'{item["index_label"].capitalize()} is required.')
        oid = oid.replace("{index}", index_value)

    print(f"\nSelected: {item['name']}")
    print(f"OID     : {oid}")
    return oid



CPU_LOAD_ROOT_OID = ".1.3.6.1.2.1.25.3.3.1.2"


def is_cpu_average_oid(oid):
    """
    The table root without a processor index means:
    WALK all logical processors and calculate the arithmetic mean.
    """
    return normalize_oid(oid) == normalize_oid(CPU_LOAD_ROOT_OID)


def snmp_cpu_average(target, version, community):
    """
    WALK hrProcessorLoad and return:
      average_load, core_values

    core_values is a list of dictionaries:
      [{"index": 3, "load_percent": 21.0}, ...]
    """
    records = snmp_walk(
        target,
        version,
        community,
        CPU_LOAD_ROOT_OID,
    )

    cores = []

    for oid, value_type, value, numeric_value in records:
        if numeric_value is None:
            continue

        try:
            load = float(numeric_value)
        except (TypeError, ValueError):
            continue

        # hrProcessorLoad is defined as a percentage, normally 0..100.
        # Ignore obviously invalid values rather than corrupting the average.
        if load < 0 or load > 100:
            continue

        try:
            index = int(normalize_oid(oid).split(".")[-1])
        except (ValueError, IndexError):
            index = None

        cores.append({
            "index": index,
            "load_percent": load,
        })

    if not cores:
        raise RuntimeError(
            "No numeric hrProcessorLoad entries were returned. "
            "The target may not expose HOST-RESOURCES-MIB CPU data."
        )

    average = statistics.fmean(core["load_percent"] for core in cores)
    minimum = min(core["load_percent"] for core in cores)
    maximum = max(core["load_percent"] for core in cores)

    return average, cores, minimum, maximum



PHYSICAL_MEMORY_ROOT_OID = ".1.3.6.1.2.1.25.2.3"
HR_STORAGE_DESCR_ROOT = ".1.3.6.1.2.1.25.2.3.1.3"
HR_STORAGE_TYPE_ROOT = ".1.3.6.1.2.1.25.2.3.1.2"
HR_STORAGE_ALLOC_ROOT = ".1.3.6.1.2.1.25.2.3.1.4"
HR_STORAGE_SIZE_ROOT = ".1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED_ROOT = ".1.3.6.1.2.1.25.2.3.1.6"
HR_STORAGE_RAM_TYPE = "1.3.6.1.2.1.25.2.1.2"


def is_physical_memory_auto_oid(oid):
    return normalize_oid(oid) == normalize_oid(PHYSICAL_MEMORY_ROOT_OID)


def _extract_oid_from_value(value):
    """Extract a dotted OID embedded in an OBJECTID value."""
    if value is None:
        return ""
    m = re.search(r"(\d+(?:\.\d+)+)", str(value))
    return m.group(1) if m else ""


def discover_physical_memory_index(target, version, community):
    """
    Find the HOST-RESOURCES-MIB storage row representing physical RAM.

    Prefer hrStorageDescr containing 'Physical Memory'. If that wording is not
    present, fall back to hrStorageType == hrStorageRam.
    """
    descr_records = snmp_walk(
        target, version, community, HR_STORAGE_DESCR_ROOT
    )

    # First choice: human-readable description.
    for oid, value_type, value, numeric_value in descr_records:
        cleaned = str(value).strip().strip('"').lower()
        if "physical memory" in cleaned or cleaned in ("physical ram", "ram"):
            try:
                return int(normalize_oid(oid).split(".")[-1]), str(value).strip().strip('"')
            except (ValueError, IndexError):
                pass

    # Fallback: storage type row identifying hrStorageRam.
    type_records = snmp_walk(
        target, version, community, HR_STORAGE_TYPE_ROOT
    )

    for oid, value_type, value, numeric_value in type_records:
        embedded = _extract_oid_from_value(value)
        if normalize_oid(embedded) == HR_STORAGE_RAM_TYPE:
            try:
                return int(normalize_oid(oid).split(".")[-1]), "Physical Memory"
            except (ValueError, IndexError):
                pass

    raise RuntimeError(
        "Could not identify a Physical Memory entry in hrStorageTable. "
        "Try walking .1.3.6.1.2.1.25.2.3.1.3 manually."
    )


def get_physical_memory_snapshot(target, version, community, known_index=None):
    """
    Return current physical-memory information from HOST-RESOURCES-MIB.

    Returns a dictionary with:
      index
      description
      storage_type
      allocation_unit_bytes
      total_bytes / used_bytes / free_bytes
      total_mib / used_mib / free_mib
      total_gib / used_gib / free_gib
      usage_percent / free_percent
    """
    if known_index is None:
        index, description = discover_physical_memory_index(
            target, version, community
        )
    else:
        index = int(known_index)
        description = "Physical Memory"

    type_rec = snmp_get(
        target, version, community, f"{HR_STORAGE_TYPE_ROOT}.{index}"
    )
    descr_rec = snmp_get(
        target, version, community, f"{HR_STORAGE_DESCR_ROOT}.{index}"
    )
    alloc_rec = snmp_get(
        target, version, community, f"{HR_STORAGE_ALLOC_ROOT}.{index}"
    )
    size_rec = snmp_get(
        target, version, community, f"{HR_STORAGE_SIZE_ROOT}.{index}"
    )
    used_rec = snmp_get(
        target, version, community, f"{HR_STORAGE_USED_ROOT}.{index}"
    )

    description = str(descr_rec[2]).strip().strip('"') or description
    storage_type_oid = _extract_oid_from_value(type_rec[2])

    if alloc_rec[3] is None or size_rec[3] is None or used_rec[3] is None:
        raise RuntimeError(
            "Physical-memory storage row did not return numeric allocation, size, or used values."
        )

    allocation_unit_bytes = int(alloc_rec[3])
    total_units = int(size_rec[3])
    used_units = int(used_rec[3])

    total_bytes = total_units * allocation_unit_bytes
    used_bytes = used_units * allocation_unit_bytes
    free_bytes = max(0, total_bytes - used_bytes)

    total_mib = total_bytes / (1024 ** 2)
    used_mib = used_bytes / (1024 ** 2)
    free_mib = free_bytes / (1024 ** 2)

    total_gib = total_bytes / (1024 ** 3)
    used_gib = used_bytes / (1024 ** 3)
    free_gib = free_bytes / (1024 ** 3)

    usage_percent = (used_bytes / total_bytes * 100.0) if total_bytes else 0.0
    free_percent = 100.0 - usage_percent

    storage_type = (
        "Physical RAM (hrStorageRam)"
        if normalize_oid(storage_type_oid) == HR_STORAGE_RAM_TYPE
        else (storage_type_oid or str(type_rec[2]))
    )

    return {
        "index": index,
        "description": description,
        "storage_type": storage_type,
        "storage_type_oid": storage_type_oid,
        "allocation_unit_bytes": allocation_unit_bytes,
        "total_units": total_units,
        "used_units": used_units,
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "total_mib": total_mib,
        "used_mib": used_mib,
        "free_mib": free_mib,
        "total_gib": total_gib,
        "used_gib": used_gib,
        "free_gib": free_gib,
        "usage_percent": usage_percent,
        "free_percent": free_percent,
    }


def metric_spec_for_oid(oid, value_type=""):
    """
    Return plotting/capture behavior for a known OID.

    kind:
      counter_octets  -> convert counter delta to Mbps
      counter_packets -> convert counter delta to packets/s
      counter_errors  -> convert counter delta to errors/s
      counter_discards-> convert counter delta to discards/s
      percent         -> plot as %
      memory_kib      -> convert KiB to MiB
      speed_bps       -> convert bits/s to Mbps
      timeticks       -> convert hundredths of seconds to hours
      status_oper     -> plot IF-MIB operational state codes
      status_admin    -> plot IF-MIB admin state codes
      gauge           -> raw numeric value
      counter_generic -> generic units/s
      text            -> not numerically plottable
    """
    o = normalize_oid(oid)
    vt = (value_type or "").lower()

    if o == normalize_oid(CPU_LOAD_ROOT_OID):
        return {
            "name": "Average CPU load",
            "kind": "cpu_average",
            "axis": "Average CPU utilization (%)",
            "unit": "%",
        }

    if o == normalize_oid(PHYSICAL_MEMORY_ROOT_OID):
        return {
            "name": "Physical memory utilization",
            "kind": "physical_memory",
            "axis": "Physical memory used (GiB)",
            "unit": "GiB",
        }

    registered = mib.get_mib_object(oid)
    if registered is not None:
        return registered.metric()

    specs = [
        ("1.3.6.1.2.1.31.1.1.1.6.", {
            "name": "Incoming network throughput (64-bit)",
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 64,
        }),
        ("1.3.6.1.2.1.31.1.1.1.10.", {
            "name": "Outgoing network throughput (64-bit)",
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 64,
        }),
        ("1.3.6.1.2.1.2.2.1.10.", {
            "name": "Incoming network throughput",
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.16.", {
            "name": "Outgoing network throughput",
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.11.", {
            "name": "Incoming unicast packet rate",
            "kind": "counter_packets",
            "axis": "Packet rate (packets/s)",
            "unit": "packets/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.17.", {
            "name": "Outgoing unicast packet rate",
            "kind": "counter_packets",
            "axis": "Packet rate (packets/s)",
            "unit": "packets/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.14.", {
            "name": "Incoming interface error rate",
            "kind": "counter_errors",
            "axis": "Errors per second",
            "unit": "errors/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.20.", {
            "name": "Outgoing interface error rate",
            "kind": "counter_errors",
            "axis": "Errors per second",
            "unit": "errors/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.13.", {
            "name": "Incoming discard rate",
            "kind": "counter_discards",
            "axis": "Discards per second",
            "unit": "discards/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.19.", {
            "name": "Outgoing discard rate",
            "kind": "counter_discards",
            "axis": "Discards per second",
            "unit": "discards/s",
            "counter_bits": 32,
        }),
        ("1.3.6.1.2.1.2.2.1.8.", {
            "name": "Interface operational status",
            "kind": "status_oper",
            "axis": "Operational status",
            "unit": "state",
        }),
        ("1.3.6.1.2.1.2.2.1.7.", {
            "name": "Interface administrative status",
            "kind": "status_admin",
            "axis": "Administrative status",
            "unit": "state",
        }),
        ("1.3.6.1.2.1.2.2.1.5.", {
            "name": "Interface reported speed",
            "kind": "speed_bps",
            "axis": "Interface speed (Mbps)",
            "unit": "Mbps",
        }),
        ("1.3.6.1.2.1.25.3.3.1.2.", {
            "name": "CPU load",
            "kind": "percent",
            "axis": "CPU load (%)",
            "unit": "%",
        }),
    ]

    for prefix, spec in specs:
        if o.startswith(prefix):
            return dict(spec)

    exact = {
        "1.3.6.1.2.1.1.3.0": {
            "name": "System uptime",
            "kind": "timeticks",
            "axis": "System uptime (hours)",
            "unit": "hours",
        },
        "1.3.6.1.2.1.25.2.2.0": {
            "name": "Total physical memory",
            "kind": "memory_kib",
            "axis": "Physical memory (MiB)",
            "unit": "MiB",
        },
        "1.3.6.1.4.1.2021.11.11.0": {
            "name": "CPU idle",
            "kind": "percent",
            "axis": "CPU idle (%)",
            "unit": "%",
        },
        "1.3.6.1.4.1.2021.10.1.3.1": {
            "name": "1-minute load average",
            "kind": "gauge",
            "axis": "Load average",
            "unit": "",
        },
        "1.3.6.1.4.1.2021.4.6.0": {
            "name": "Available physical memory",
            "kind": "memory_kib",
            "axis": "Available memory (MiB)",
            "unit": "MiB",
        },
    }

    if o in exact:
        return dict(exact[o])

    # Infer an unknown numeric object's basic behavior from ASN.1 type.
    if "counter64" in vt:
        return {
            "name": "SNMP counter rate",
            "kind": "counter_generic",
            "axis": "Counter rate (units/s)",
            "unit": "units/s",
            "counter_bits": 64,
        }
    if "counter32" in vt:
        return {
            "name": "SNMP counter rate",
            "kind": "counter_generic",
            "axis": "Counter rate (units/s)",
            "unit": "units/s",
            "counter_bits": 32,
        }
    if any(t in vt for t in ("integer", "gauge", "unsigned", "timeticks")):
        return {
            "name": "SNMP numeric value",
            "kind": "gauge",
            "axis": "SNMP value",
            "unit": "",
        }

    return {
        "name": "SNMP value",
        "kind": "gauge",
        "axis": "SNMP numeric value",
        "unit": "",
    }


def is_counter_kind(kind):
    return kind in {
        "counter_octets",
        "counter_bytes",
        "counter_packets",
        "counter_errors",
        "counter_discards",
        "counter_generic",
    }


def transform_sample_value(metric, numeric_value):
    """Convert a raw non-counter SNMP number into a graph-friendly value."""
    if numeric_value is None:
        return None

    kind = metric["kind"]
    if kind == "text":
        return None
    value = float(numeric_value)

    if kind == "memory_kib":
        return value / 1024.0
    if kind == "bytes_gauge":
        return value / (1024 ** 3)
    if kind == "speed_bps":
        return value / 1_000_000.0
    if kind == "timeticks":
        return value / 100.0 / 3600.0

    return value


def counter_rate(metric, delta, seconds):
    """Convert a counter delta into a meaningful per-second metric."""
    if delta is None or seconds <= 0:
        return None

    kind = metric["kind"]

    if kind == "counter_octets":
        return (delta / seconds) * 8 / 1_000_000
    if kind == "counter_bytes":
        return delta / seconds / (1024 ** 2)
    if kind in ("counter_packets", "counter_errors", "counter_discards", "counter_generic"):
        return delta / seconds

    return None


def format_metric_value(metric, value):
    if value is None:
        return "N/A"

    unit = metric.get("unit", "")
    kind = metric.get("kind", "")

    if kind in ("status_oper", "status_admin"):
        mapping = STATUS_OPER if kind == "status_oper" else STATUS_ADMIN
        state = mapping.get(int(value), f"State {int(value)}")
        return f"{state} ({int(value)})"

    if unit == "%":
        return f"{value:.1f}%"
    if unit == "MiB":
        return f"{value:.2f} MiB"
    if unit == "Mbps":
        return f"{value:.3f} Mbps"
    if unit == "hours":
        return f"{value:.3f} h"
    if unit:
        return f"{value:.3f} {unit}"
    return f"{value:.6g}"


# ---------------------------------------------------------------------------
# SNMP operations
# ---------------------------------------------------------------------------

def snmp_get(target, version, community, oid):
    args = build_common_args("snmpget", version, community, target)
    args.append(oid)
    output = run_command(args)

    if not output:
        raise RuntimeError("No response returned.")

    # snmpget should normally return one line.
    line = output.splitlines()[0]
    return parse_snmp_line(line)


def snmp_collection_interval(target, version, community, oid):
    """Read this agent's effective collection interval for a selected metric."""
    group = collection_group_for_oid(oid)
    if group is None:
        raise RuntimeError("This OID has no known agent collection schedule; supply a polling interval.")
    record = snmp_get(target, version, community, oid_text(collection_metadata_oid(group, 2)))
    if record[3] is None:
        raise RuntimeError("The agent did not expose its collection interval; use normal polling.")
    try:
        return positive_seconds(record[3])
    except ValueError as exc:
        raise RuntimeError("The agent returned an invalid collection interval.") from exc


def snmp_walk(target, version, community, oid):
    args = build_common_args("snmpwalk", version, community, target)
    args.append(oid)
    output = run_command(args)

    rows = []
    for line in output.splitlines():
        if line.strip():
            rows.append(parse_snmp_line(line))
    return rows


def snmp_set(target, version, community, oid, data_type, value):
    # snmpset syntax:
    # snmpset ... TARGET OID TYPE VALUE
    args = build_common_args("snmpset", version, community, target)
    args.extend([oid, data_type, value])
    output = run_command(args)

    if not output:
        raise RuntimeError("No response returned.")

    return parse_snmp_line(output.splitlines()[0])


# ---------------------------------------------------------------------------
# CSV functions
# ---------------------------------------------------------------------------

STANDARD_FIELDS = [
    "timestamp",
    "target",
    "operation",
    "oid",
    "key",
    "value_type",
    "value",
    "numeric_value",
]


def save_standard_result(path, target, operation, records):
    """
    Save GET/WALK/SET records as key-value-style rows.
    OID is treated as the key; returned SNMP content is the value.
    """
    timestamp = now_iso()
    rows = []

    for oid, value_type, value, numeric_value in records:
        rows.append({
            "timestamp": timestamp,
            "target": target,
            "operation": operation,
            "oid": oid,
            "key": oid,
            "value_type": value_type,
            "value": value,
            "numeric_value": "" if numeric_value is None else numeric_value,
        })

    append_rows(path, STANDARD_FIELDS, rows)


CAPTURE_FIELDS = [
    "timestamp",
    "elapsed_seconds",
    "target",
    "oid",
    "metric_name",
    "metric_kind",
    "metric_unit",
    "value_type",
    "value",
    "numeric_value",
    "display_value",
    "display_unit",
    "status",
    "delta",
    "update_interval_seconds",
    "rate",
    "rate_unit",
    # Legacy throughput columns retained for compatibility.
    "bytes_per_second",
    "bits_per_second",
    "mbps",
    "cpu_core_count",
    "cpu_core_min_percent",
    "cpu_core_max_percent",
    "cpu_core_values",
    "memory_index",
    "memory_description",
    "memory_type",
    "memory_allocation_unit_bytes",
    "memory_total_mib",
    "memory_used_mib",
    "memory_free_mib",
    "memory_total_gib",
    "memory_used_gib",
    "memory_free_gib",
    "memory_usage_percent",
    "memory_free_percent",
]

INTERVAL_FIELDS = [
    "interval_start_timestamp",
    "interval_end_timestamp",
    "interval_start_seconds",
    "interval_end_seconds",
    "interval_seconds",
    "target",
    "oid",
    "metric_name",
    "metric_kind",
    "rate_unit",
    "counter_start",
    "counter_end",
    "delta",
    "rate",
    # Legacy throughput columns retained for compatibility.
    "delta_bytes",
    "average_bytes_per_second",
    "average_bits_per_second",
    "average_mbps",
]


def infer_counter_bits(value_type="", oid="", requested_bits="auto"):
    """
    Determine whether an SNMP counter is 32-bit or 64-bit.
    """
    vt = (value_type or "").lower()
    metric = metric_spec_for_oid(oid, value_type)

    if "counter64" in vt:
        return 64
    if "counter32" in vt:
        return 32

    if metric.get("counter_bits") in (32, 64):
        return metric["counter_bits"]

    if requested_bits in (32, 64):
        return requested_bits

    return 32


def calculate_counter_delta(previous, current, counter_bits):
    """
    Calculate the difference between cumulative counters, including one wrap.
    """
    if previous is None or current is None:
        return None

    if current >= previous:
        return current - previous

    bits = int(counter_bits)
    if bits not in (32, 64):
        return None

    modulus = 2 ** bits
    return (modulus - previous) + current


def recalculate_interval_measurement(
    counter_start, counter_end, seconds, oid="", value_type="", bits="auto"
):
    metric = metric_spec_for_oid(oid, value_type)
    effective_bits = infer_counter_bits(value_type, oid, bits)
    delta = calculate_counter_delta(counter_start, counter_end, effective_bits)

    if delta is None or seconds <= 0:
        return None, None, metric, effective_bits

    rate = counter_rate(metric, delta, seconds)
    return delta, rate, metric, effective_bits


def continuous_capture(target, version, community, oid, interval,
                       output_file, counter_bits="auto", duration=None, sync_agent=False):
    """
    Poll any supported SNMP OID repeatedly.

    Special CPU aggregate mode:
      WALKs hrProcessorLoad on every cycle and averages all logical CPUs.

    Special physical-memory mode:
      Automatically finds the Physical Memory hrStorageTable row, then polls
      total/used memory and calculates free memory plus utilization percentage.

    Counter OIDs:
      derive rates using time between actual counter changes.

    Gauge/status OIDs:
      store every successful poll directly.
    """
    interval = positive_seconds(interval)
    if duration is not None:
        duration = positive_seconds(duration)
    if sync_agent:
        interval = snmp_collection_interval(target, version, community, oid)
    output_file = Path(output_file).expanduser()
    interval_file = output_file.with_name(output_file.stem + "_intervals.csv")
    cpu_average_mode = is_cpu_average_oid(oid)
    memory_mode = is_physical_memory_auto_oid(oid)

    print("\nStarting capture...")
    print(f"Target       : {target}")
    print(f"OID          : {oid}")
    print(f"Poll interval: {interval} second(s)")
    print(f"Raw CSV      : {output_file}")
    if sync_agent:
        print("Polling mode : follow the agent's live collection interval")

    memory_index = None
    memory_description = None

    if cpu_average_mode:
        print("Metric       : Average CPU load across all logical processors")
        print("Method       : SNMP WALK hrProcessorLoad table on every poll")
    elif memory_mode:
        print("Metric       : Physical RAM utilization")
        print("Method       : HOST-RESOURCES-MIB hrStorageTable")
        memory_index, memory_description = discover_physical_memory_index(
            target, version, community
        )
        first_memory = get_physical_memory_snapshot(
            target, version, community, known_index=memory_index
        )
        print(f"RAM index    : {memory_index}")
        print(f"RAM entry    : {first_memory['description']}")
        print(f"RAM type     : {first_memory['storage_type']}")
        print(f"Total RAM    : {first_memory['total_gib']:.3f} GiB")
        print(
            "Note         : SNMP storage type identifies RAM, but not DDR generation "
            "or motherboard maximum supported capacity."
        )
    else:
        print("Metric       : detected from OID / SNMP response")

    if duration is None:
        print("Stop with Ctrl+C.")
    else:
        print(f"Duration     : {duration} second(s)")
    print()

    start_monotonic = time.monotonic()
    next_sample = start_monotonic
    last_poll_time = None

    last_changed_value = None
    last_changed_time = None
    last_changed_elapsed = None
    last_changed_wall = None

    sample_number = 0
    update_number = 0
    announced_metric = False
    active_metric = None
    interrupted = False

    try:
        while True:
            current_monotonic = time.monotonic()
            elapsed = current_monotonic - start_monotonic

            if duration is not None and elapsed >= duration:
                break

            if sync_agent:
                updated_interval = snmp_collection_interval(target, version, community, oid)
                if updated_interval != interval:
                    interval = updated_interval
                    next_sample = (
                        last_poll_time + interval if last_poll_time is not None else current_monotonic
                    )
                    print(f"Agent collection interval changed to {interval:g} second(s).")
                current_monotonic = time.monotonic()
                elapsed = current_monotonic - start_monotonic
                if duration is not None and elapsed >= duration:
                    break

            if current_monotonic < next_sample:
                wait = next_sample - current_monotonic
                if sync_agent:
                    wait = min(wait, 1.0)
                if duration is not None:
                    wait = min(wait, duration - elapsed)
                time.sleep(wait)
                continue

            sample_time = time.monotonic()
            elapsed = sample_time - start_monotonic
            wall_timestamp = now_iso()
            last_poll_time = sample_time

            cpu_cores = []
            cpu_min = None
            cpu_max = None
            memory = None

            try:
                if cpu_average_mode:
                    numeric_value, cpu_cores, cpu_min, cpu_max = snmp_cpu_average(
                        target, version, community
                    )
                    actual_oid = CPU_LOAD_ROOT_OID
                    value_type = "AggregateGauge"
                    value = f"{numeric_value:.3f}"

                elif memory_mode:
                    memory = get_physical_memory_snapshot(
                        target,
                        version,
                        community,
                        known_index=memory_index,
                    )
                    # The main numeric series is USED GiB.
                    numeric_value = memory["used_gib"]
                    actual_oid = PHYSICAL_MEMORY_ROOT_OID
                    value_type = "PhysicalMemoryAggregate"
                    value = f"{numeric_value:.6f}"

                else:
                    actual_oid, value_type, value, numeric_value = snmp_get(
                        target, version, community, oid
                    )

            except RuntimeError as exc:
                print(f"[{elapsed:8.2f}s] ERROR: {exc}", file=sys.stderr)
                next_sample += interval
                continue

            sample_number += 1
            active_metric = metric_spec_for_oid(actual_oid or oid, value_type)

            if not announced_metric:
                print(f"Detected metric: {active_metric['name']}")
                print(f"Metric type    : {active_metric['kind']}")

                if cpu_average_mode:
                    print(f"Logical CPUs found: {len(cpu_cores)}")
                    core_indexes = [
                        str(core["index"]) for core in cpu_cores
                        if core["index"] is not None
                    ]
                    if core_indexes:
                        print("CPU indexes       : " + ", ".join(core_indexes))

                if is_counter_kind(active_metric["kind"]):
                    print(f"Interval CSV   : {interval_file}")
                    print("Rate method    : actual counter-change interval")
                    print(
                        "Counter width   : "
                        + str(infer_counter_bits(
                            value_type, actual_oid or oid, counter_bits
                        ))
                        + "-bit"
                    )

                print()
                announced_metric = True

            status = "sample" if numeric_value is not None else "non-numeric"
            delta = None
            update_interval = None
            rate = None
            rate_unit = active_metric.get("unit", "")
            display_value = transform_sample_value(active_metric, numeric_value)
            display_unit = active_metric.get("unit", "")

            bytes_per_second = None
            bits_per_second = None
            mbps = None

            if numeric_value is not None and is_counter_kind(active_metric["kind"]):
                if last_changed_value is None:
                    status = "initial"
                    last_changed_value = numeric_value
                    last_changed_time = sample_time
                    last_changed_elapsed = elapsed
                    last_changed_wall = wall_timestamp

                elif numeric_value == last_changed_value:
                    status = "unchanged"

                else:
                    status = "updated"
                    effective_bits = infer_counter_bits(
                        value_type=value_type,
                        oid=actual_oid or oid,
                        requested_bits=counter_bits,
                    )
                    old_value = last_changed_value
                    delta = calculate_counter_delta(
                        old_value, numeric_value, effective_bits
                    )
                    update_interval = sample_time - last_changed_time
                    rate = counter_rate(active_metric, delta, update_interval)
                    rate_unit = active_metric.get("unit", "units/s")

                    if active_metric["kind"] == "counter_octets" and rate is not None:
                        mbps = rate
                        bits_per_second = rate * 1_000_000
                        bytes_per_second = bits_per_second / 8

                    if rate is not None:
                        interval_row = {
                            "interval_start_timestamp": last_changed_wall,
                            "interval_end_timestamp": wall_timestamp,
                            "interval_start_seconds": f"{last_changed_elapsed:.6f}",
                            "interval_end_seconds": f"{elapsed:.6f}",
                            "interval_seconds": f"{update_interval:.6f}",
                            "target": target,
                            "oid": actual_oid or oid,
                            "metric_name": active_metric["name"],
                            "metric_kind": active_metric["kind"],
                            "rate_unit": rate_unit,
                            "counter_start": old_value,
                            "counter_end": numeric_value,
                            "delta": delta,
                            "rate": f"{rate:.9f}",
                            "delta_bytes": (
                                delta if active_metric["kind"] == "counter_octets" else ""
                            ),
                            "average_bytes_per_second": (
                                f"{bytes_per_second:.6f}"
                                if bytes_per_second is not None else ""
                            ),
                            "average_bits_per_second": (
                                f"{bits_per_second:.6f}"
                                if bits_per_second is not None else ""
                            ),
                            "average_mbps": (
                                f"{mbps:.9f}" if mbps is not None else ""
                            ),
                        }
                        append_rows(interval_file, INTERVAL_FIELDS, [interval_row])
                        update_number += 1

                    rollover = numeric_value < old_value
                    last_changed_value = numeric_value
                    last_changed_time = sample_time
                    last_changed_elapsed = elapsed
                    last_changed_wall = wall_timestamp

            raw_row = {
                "timestamp": wall_timestamp,
                "elapsed_seconds": f"{elapsed:.6f}",
                "target": target,
                "oid": actual_oid or oid,
                "metric_name": active_metric["name"],
                "metric_kind": active_metric["kind"],
                "metric_unit": active_metric.get("unit", ""),
                "value_type": value_type,
                "value": value,
                "numeric_value": "" if numeric_value is None else numeric_value,
                "display_value": (
                    "" if display_value is None else f"{display_value:.9f}"
                ),
                "display_unit": display_unit,
                "status": status,
                "delta": "" if delta is None else delta,
                "update_interval_seconds": (
                    "" if update_interval is None else f"{update_interval:.6f}"
                ),
                "rate": "" if rate is None else f"{rate:.9f}",
                "rate_unit": "" if rate is None else rate_unit,
                "bytes_per_second": (
                    "" if bytes_per_second is None else f"{bytes_per_second:.6f}"
                ),
                "bits_per_second": (
                    "" if bits_per_second is None else f"{bits_per_second:.6f}"
                ),
                "mbps": "" if mbps is None else f"{mbps:.9f}",
                "cpu_core_count": len(cpu_cores) if cpu_average_mode else "",
                "cpu_core_min_percent": (
                    f"{cpu_min:.3f}" if cpu_min is not None else ""
                ),
                "cpu_core_max_percent": (
                    f"{cpu_max:.3f}" if cpu_max is not None else ""
                ),
                "cpu_core_values": (
                    json.dumps(cpu_cores, separators=(",", ":"))
                    if cpu_average_mode else ""
                ),
                "memory_index": memory["index"] if memory else "",
                "memory_description": memory["description"] if memory else "",
                "memory_type": memory["storage_type"] if memory else "",
                "memory_allocation_unit_bytes": (
                    memory["allocation_unit_bytes"] if memory else ""
                ),
                "memory_total_mib": (
                    f"{memory['total_mib']:.6f}" if memory else ""
                ),
                "memory_used_mib": (
                    f"{memory['used_mib']:.6f}" if memory else ""
                ),
                "memory_free_mib": (
                    f"{memory['free_mib']:.6f}" if memory else ""
                ),
                "memory_total_gib": (
                    f"{memory['total_gib']:.9f}" if memory else ""
                ),
                "memory_used_gib": (
                    f"{memory['used_gib']:.9f}" if memory else ""
                ),
                "memory_free_gib": (
                    f"{memory['free_gib']:.9f}" if memory else ""
                ),
                "memory_usage_percent": (
                    f"{memory['usage_percent']:.6f}" if memory else ""
                ),
                "memory_free_percent": (
                    f"{memory['free_percent']:.6f}" if memory else ""
                ),
            }

            append_rows(output_file, CAPTURE_FIELDS, [raw_row])

            if memory_mode and memory is not None:
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"RAM used={memory['used_gib']:.3f}/{memory['total_gib']:.3f} GiB "
                    f"({memory['usage_percent']:.1f}%) | "
                    f"free={memory['free_gib']:.3f} GiB "
                    f"({memory['free_percent']:.1f}%)"
                )

            elif cpu_average_mode and numeric_value is not None:
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"CPU avg={numeric_value:6.2f}% | "
                    f"logical CPUs={len(cpu_cores)} | "
                    f"min={cpu_min:5.1f}% | max={cpu_max:5.1f}%"
                )

            elif status == "initial":
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"value={numeric_value:<15} INITIAL COUNTER BASELINE"
                )

            elif status == "unchanged":
                age = sample_time - last_changed_time
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"value={numeric_value:<15} unchanged "
                    f"({age:.1f}s since last counter change)"
                )

            elif status == "updated" and rate is not None:
                rollover_text = ""
                if 'rollover' in locals() and rollover:
                    rollover_text = " | rollover"

                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"value={numeric_value:<15} UPDATED | "
                    f"delta={delta:,} | window={update_interval:.2f}s | "
                    f"avg={rate:.3f} {rate_unit}{rollover_text}"
                )

            elif status == "sample":
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"{active_metric['name']}="
                    f"{format_metric_value(active_metric, display_value)}"
                )

            else:
                print(
                    f"[{elapsed:8.2f}s] sample={sample_number:<5} "
                    f"value={value} ({status})"
                )

            next_sample += interval
            now = time.monotonic()
            if next_sample <= now:
                next_sample = now + interval

    except KeyboardInterrupt:
        print("\nCapture stopped by user.")
        interrupted = True

    if not sample_number and not interrupted:
        raise RuntimeError("Capture finished without a successful sample.")

    if sample_number:
        print(f"\nSaved {sample_number} raw poll(s) to: {output_file}")
    else:
        print("\nNo samples were written.")

    if memory_mode:
        print(
            "Each row contains total, used and free physical memory "
            "in MiB/GiB plus used/free percentages."
        )
    elif cpu_average_mode:
        print(
            "Each CSV row contains overall average CPU load plus "
            "per-CPU values in cpu_core_values."
        )
    elif active_metric and is_counter_kind(active_metric["kind"]):
        if update_number:
            print(f"Saved {update_number} derived interval(s) to: {interval_file}")
        else:
            print("No counter changes were observed, so no interval-rate rows were written.")
    else:
        print("This metric is a gauge/state value, so every raw CSV row is directly plottable.")

    return 130 if interrupted else 0



# ---------------------------------------------------------------------------
# Plotting/statistics
# ---------------------------------------------------------------------------

def read_numeric_column(rows, column):
    x = []
    y = []

    for index, row in enumerate(rows):
        raw = row.get(column, "").strip()
        if raw == "":
            continue

        try:
            value = float(raw)
        except ValueError:
            continue

        elapsed_raw = row.get("elapsed_seconds", "")
        try:
            elapsed = float(elapsed_raw)
        except (ValueError, TypeError):
            elapsed = float(index)

        x.append(elapsed)
        y.append(value)

    return x, y


def print_statistics(values, label):
    if not values:
        print("No numeric data available for statistics.")
        return

    print("\nCapture statistics")
    print("------------------")
    print(f"Metric : {label}")
    print(f"Samples: {len(values)}")
    print(f"Min    : {min(values):.6f}")
    print(f"Max    : {max(values):.6f}")
    print(f"Mean   : {statistics.fmean(values):.6f}")
    print(f"Median : {statistics.median(values):.6f}")

    if len(values) > 1:
        print(f"Std dev: {statistics.stdev(values):.6f}")


def plot_choice(prompt, selected=None, *, interactive=True):
    return str(selected) if selected is not None else input(prompt).strip() if interactive else ""


def plotting_backend(headless=False):
    try:
        import matplotlib
        if headless:
            matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Matplotlib is missing. Install requirements-manager.txt in your Python environment.") from exc
    return plt


def save_plot(plt, figure, default_output, output=None, show=None, *, interactive=True, comparison=False):
    try:
        selected = plot_choice(f"PNG output [{default_output}]: ", output, interactive=interactive)
        path = Path(selected).expanduser() if selected else default_output
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, dpi=220, bbox_inches="tight")
        print(f"{'Comparison' if comparison else 'Detailed'} plot saved to: {path}")
        if show is None:
            show = interactive and plot_choice("Open the plot window now? [Y/n]: ").lower() not in ("n", "no")
        if show:
            plt.show()
        return path
    finally:
        plt.close(figure)


def plot_csv(path, output=None, *, show=None, color=None, scale=None, memory_mode=None, interactive=True):
    path = Path(path).expanduser()
    plt = plotting_backend(show is False or (not interactive and show is not True))

    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        raise RuntimeError("CSV file contains no data.")

    x = []
    y = []
    repaired_rollovers = 0
    metric = None
    plot_title = "SNMP measurement"
    axis_label = "SNMP value"
    unit = ""
    source_mode = "raw"

    # ---------------------------------------------------------------
    # v6 generic interval CSV: counter-derived rate
    # ---------------------------------------------------------------
    if (
        "rate" in fieldnames
        and "counter_start" in fieldnames
        and "counter_end" in fieldnames
        and "interval_seconds" in fieldnames
    ):
        source_mode = "interval"
        first_oid = rows[0].get("oid", "")
        first_kind = rows[0].get("metric_kind", "")
        metric = metric_spec_for_oid(first_oid)

        # Preserve the stored v6 metric kind/name if available.
        if first_kind:
            metric["kind"] = first_kind
        if rows[0].get("metric_name"):
            metric["name"] = rows[0]["metric_name"]
        if rows[0].get("rate_unit"):
            metric["unit"] = rows[0]["rate_unit"]

        for row in rows:
            try:
                start = float(row["interval_start_seconds"])
                end = float(row["interval_end_seconds"])
                seconds = float(row["interval_seconds"])
                counter_start = parse_counter_value(row["counter_start"])
                counter_end = parse_counter_value(row["counter_end"])
                oid = row.get("oid", first_oid)
                value_type = row.get("value_type", "")
            except (ValueError, KeyError, TypeError):
                continue

            row_metric = metric_spec_for_oid(oid, value_type)
            if row.get("metric_kind"):
                row_metric["kind"] = row["metric_kind"]
            if row.get("metric_name"):
                row_metric["name"] = row["metric_name"]
            if row.get("rate_unit"):
                row_metric["unit"] = row["rate_unit"]

            effective_bits = infer_counter_bits(value_type, oid, "auto")
            delta = calculate_counter_delta(
                counter_start, counter_end, effective_bits
            )
            rate = counter_rate(row_metric, delta, seconds)

            if rate is None:
                # Generic fallback for an older/unknown interval row.
                try:
                    rate = float(row["rate"])
                except (ValueError, TypeError):
                    continue

            if counter_end < counter_start and effective_bits == 32:
                repaired_rollovers += 1

            x.append((start + end) / 2.0)
            y.append(rate)
            metric = row_metric

        if not y:
            raise RuntimeError("No usable interval-rate measurements were found.")

        axis_label = metric.get("axis", metric.get("unit", "Rate"))
        unit = metric.get("unit", "")
        plot_title = metric.get("name", "SNMP counter rate")

    # ---------------------------------------------------------------
    # Legacy v2-v5 interval CSV
    # ---------------------------------------------------------------
    elif (
        "average_mbps" in fieldnames
        and "interval_seconds" in fieldnames
        and "counter_start" in fieldnames
        and "counter_end" in fieldnames
    ):
        source_mode = "interval"
        first_oid = rows[0].get("oid", "")
        metric = metric_spec_for_oid(first_oid)
        # Older interval files were throughput-only.
        metric = {
            "name": metric.get("name", "Network throughput"),
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 32,
        }

        for row in rows:
            try:
                start = float(row["interval_start_seconds"])
                end = float(row["interval_end_seconds"])
                seconds = float(row["interval_seconds"])
                counter_start = parse_counter_value(row["counter_start"])
                counter_end = parse_counter_value(row["counter_end"])
                oid = row.get("oid", first_oid)
            except (ValueError, KeyError, TypeError):
                continue

            effective_bits = infer_counter_bits("", oid, "auto")
            delta = calculate_counter_delta(
                counter_start, counter_end, effective_bits
            )
            rate = counter_rate(metric, delta, seconds)

            if rate is None:
                continue

            if counter_end < counter_start and effective_bits == 32:
                repaired_rollovers += 1

            x.append((start + end) / 2.0)
            y.append(rate)

        if not y:
            raise RuntimeError("No usable legacy interval-rate data found.")

        axis_label = "Average throughput (Mbps)"
        unit = "Mbps"
        plot_title = "SNMP Average Network Throughput"

    # ---------------------------------------------------------------
    # Physical-memory capture CSV
    # ---------------------------------------------------------------
    elif (
        "elapsed_seconds" in fieldnames
        and "memory_total_gib" in fieldnames
        and "memory_used_gib" in fieldnames
        and any(str(r.get("memory_total_gib", "")).strip() for r in rows)
    ):
        source_mode = "memory"
        metric = {
            "name": "Physical memory utilization",
            "kind": "physical_memory",
            "axis": "Physical memory (GiB)",
            "unit": "GiB",
        }

        memory_x = []
        memory_used = []
        memory_free = []
        memory_total = []
        memory_percent = []

        for row in rows:
            try:
                elapsed = float(row["elapsed_seconds"])
                total = float(row["memory_total_gib"])
                used = float(row["memory_used_gib"])
                free = float(row["memory_free_gib"])
                percent = float(row["memory_usage_percent"])
            except (ValueError, TypeError, KeyError):
                continue

            memory_x.append(elapsed)
            memory_total.append(total)
            memory_used.append(used)
            memory_free.append(free)
            memory_percent.append(percent)

        if not memory_x:
            raise RuntimeError("No physical-memory measurements were available to plot.")

        print("\nPhysical-memory graph mode")
        print("--------------------------")
        print("1. Used RAM (GiB) with 0 -> total physical RAM Y-axis")
        print("2. RAM utilization (%) with 0 -> 100% Y-axis")
        print("3. Used and free RAM (GiB) together")
        memory_plot_choice = plot_choice("Choose [1]: ", {"used": "1", "percent": "2", "both": "3"}.get(memory_mode),
                                        interactive=interactive) or "1"

        color_options = {
            "1": ("blue", "Blue"),
            "2": ("red", "Red"),
            "3": ("yellow", "Yellow"),
            "4": ("green", "Green"),
            "5": ("hotpink", "Pink"),
            "6": ("black", "Black"),
        }

        print("\nGraph line colors:")
        for number, (_, display_name) in color_options.items():
            print(f"{number}. {display_name}")

        selected_color = next((number for number, (name, _) in color_options.items() if name == color), None)
        color_choice = plot_choice("Choose main line color [2 = Red]: ", selected_color, interactive=interactive) or "2"
        line_color, line_color_name = color_options.get(
            color_choice, color_options["2"]
        )

        total_capacity = max(memory_total)
        used_min = min(memory_used)
        used_max = max(memory_used)
        used_mean = statistics.fmean(memory_used)
        free_min = min(memory_free)
        free_max = max(memory_free)
        percent_min = min(memory_percent)
        percent_max = max(memory_percent)
        percent_mean = statistics.fmean(memory_percent)

        width = min(20.0, max(12.0, 9.0 + len(memory_x) * 0.12))
        fig, ax = plt.subplots(figsize=(width, 7.0))

        if memory_plot_choice == "2":
            ax.plot(
                memory_x,
                memory_percent,
                color=line_color,
                linewidth=2.0,
                marker="o",
                markersize=4.0,
                label="RAM utilization",
            )
            ax.set_ylabel("Physical memory utilization (%)")
            ax.set_ylim(0, 100)
            ax.set_title("Physical Memory Utilization")
            stats_text = (
                f"Measurements: {len(memory_percent)}\n"
                f"Total RAM: {total_capacity:.3f} GiB\n"
                f"Mean usage: {percent_mean:.2f}%\n"
                f"Minimum usage: {percent_min:.2f}%\n"
                f"Maximum usage: {percent_max:.2f}%\n"
                f"Line: {line_color_name}"
            )

        elif memory_plot_choice == "3":
            # Main selected color is used for used RAM. Free RAM is shown as
            # a black dashed line so both series remain distinguishable.
            ax.plot(
                memory_x,
                memory_used,
                color=line_color,
                linewidth=2.0,
                marker="o",
                markersize=4.0,
                label="Used RAM",
            )
            ax.plot(
                memory_x,
                memory_free,
                color="black",
                linewidth=1.8,
                linestyle="--",
                label="Free RAM",
            )
            ax.axhline(
                total_capacity,
                color="black",
                linestyle=":",
                linewidth=1.3,
                label="Total physical RAM",
            )
            ax.set_ylabel("Physical memory (GiB)")
            ax.set_ylim(0, total_capacity * 1.03 if total_capacity > 0 else 1)
            ax.set_title("Used and Free Physical Memory")
            stats_text = (
                f"Measurements: {len(memory_used)}\n"
                f"Total RAM: {total_capacity:.3f} GiB\n"
                f"Mean used: {used_mean:.3f} GiB\n"
                f"Min used: {used_min:.3f} GiB\n"
                f"Max used: {used_max:.3f} GiB\n"
                f"Min free: {free_min:.3f} GiB\n"
                f"Max free: {free_max:.3f} GiB"
            )

        else:
            ax.plot(
                memory_x,
                memory_used,
                color=line_color,
                linewidth=2.0,
                marker="o",
                markersize=4.0,
                label="Used RAM",
            )
            ax.axhline(
                total_capacity,
                color="black",
                linestyle="--",
                linewidth=1.5,
                label=f"Total physical RAM ({total_capacity:.2f} GiB)",
            )
            ax.set_ylabel("Physical memory used (GiB)")
            # This is the requested capacity-bounded graph:
            # minimum = 0 GiB, maximum = total physical RAM.
            ax.set_ylim(0, total_capacity * 1.03 if total_capacity > 0 else 1)
            ax.set_title("Physical Memory Usage")
            stats_text = (
                f"Measurements: {len(memory_used)}\n"
                f"Total RAM: {total_capacity:.3f} GiB\n"
                f"Mean used: {used_mean:.3f} GiB\n"
                f"Minimum used: {used_min:.3f} GiB\n"
                f"Maximum used: {used_max:.3f} GiB\n"
                f"Mean utilization: {percent_mean:.2f}%\n"
                f"Line: {line_color_name}"
            )

        ax.set_xlabel("Elapsed time (seconds)")
        ax.grid(True, which="major", alpha=0.3)

        if len(memory_x) > 1:
            span = max(memory_x) - min(memory_x)
            x_pad = max(1.0, span * 0.02)
            ax.set_xlim(min(memory_x) - x_pad, max(memory_x) + x_pad)

        ax.text(
            0.015,
            0.975,
            stats_text,
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=9,
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.78},
        )

        ax.legend(loc="best")
        fig.tight_layout()

        save_default = path.with_name(
            path.stem + "_Physical_Memory_plot.png"
        )
        return save_plot(plt, fig, save_default, output, show, interactive=interactive)

    # ---------------------------------------------------------------
    # Raw capture CSV (v6 or older)
    # ---------------------------------------------------------------
    elif "elapsed_seconds" in fieldnames and "numeric_value" in fieldnames:
        source_mode = "raw"
        first_numeric_row = next(
            (r for r in rows if str(r.get("numeric_value", "")).strip() != ""),
            rows[0]
        )
        first_oid = first_numeric_row.get("oid", "")
        first_vtype = first_numeric_row.get("value_type", "")
        metric = metric_spec_for_oid(first_oid, first_vtype)

        if first_numeric_row.get("metric_kind"):
            metric["kind"] = first_numeric_row["metric_kind"]
        if first_numeric_row.get("metric_name"):
            metric["name"] = first_numeric_row["metric_name"]
        if first_numeric_row.get("metric_unit"):
            metric["unit"] = first_numeric_row["metric_unit"]

        if is_counter_kind(metric["kind"]):
            # Rebuild proper counter-change intervals from the raw CSV.
            previous_value = None
            previous_elapsed = None
            previous_vtype = first_vtype

            for row in rows:
                try:
                    raw_numeric = str(row.get("numeric_value", "")).strip()
                    if raw_numeric == "":
                        continue
                    current_value = parse_counter_value(raw_numeric)
                    elapsed = float(row["elapsed_seconds"])
                    oid = row.get("oid", first_oid)
                    vtype = row.get("value_type", previous_vtype)
                except (ValueError, TypeError):
                    continue

                if previous_value is None:
                    previous_value = current_value
                    previous_elapsed = elapsed
                    previous_vtype = vtype
                    continue

                if current_value == previous_value:
                    continue

                seconds = elapsed - previous_elapsed
                if seconds <= 0:
                    previous_value = current_value
                    previous_elapsed = elapsed
                    previous_vtype = vtype
                    continue

                row_metric = metric_spec_for_oid(oid, vtype)
                if row.get("metric_kind"):
                    row_metric["kind"] = row["metric_kind"]
                if row.get("metric_name"):
                    row_metric["name"] = row["metric_name"]
                if row.get("metric_unit"):
                    row_metric["unit"] = row["metric_unit"]

                effective_bits = infer_counter_bits(vtype, oid, "auto")
                delta = calculate_counter_delta(
                    previous_value, current_value, effective_bits
                )
                rate = counter_rate(row_metric, delta, seconds)

                if rate is not None:
                    x.append((previous_elapsed + elapsed) / 2.0)
                    y.append(rate)
                    metric = row_metric

                    if current_value < previous_value and effective_bits == 32:
                        repaired_rollovers += 1

                previous_value = current_value
                previous_elapsed = elapsed
                previous_vtype = vtype

            if not y:
                print("No counter changes were available to plot.")
                return

            axis_label = metric.get("axis", "Counter rate")
            unit = metric.get("unit", "")
            plot_title = metric.get("name", "SNMP counter rate")

        else:
            # Gauge/status/CPU/memory/uptime: every numeric sample is useful.
            for row in rows:
                try:
                    raw_numeric = str(row.get("numeric_value", "")).strip()
                    if raw_numeric == "":
                        continue
                    numeric_value = float(raw_numeric)
                    elapsed = float(row["elapsed_seconds"])
                    oid = row.get("oid", first_oid)
                    vtype = row.get("value_type", first_vtype)
                except (ValueError, TypeError):
                    continue

                row_metric = metric_spec_for_oid(oid, vtype)
                if row.get("metric_kind"):
                    row_metric["kind"] = row["metric_kind"]
                if row.get("metric_name"):
                    row_metric["name"] = row["metric_name"]
                if row.get("metric_unit"):
                    row_metric["unit"] = row["metric_unit"]

                # Prefer v6's already transformed display_value.
                display_raw = str(row.get("display_value", "")).strip()
                if display_raw:
                    try:
                        plot_value = float(display_raw)
                    except ValueError:
                        plot_value = transform_sample_value(row_metric, numeric_value)
                else:
                    plot_value = transform_sample_value(row_metric, numeric_value)

                if plot_value is None:
                    continue

                x.append(elapsed)
                y.append(plot_value)
                metric = row_metric

            if not y:
                raise RuntimeError("No numeric gauge/state samples were available to plot.")

            axis_label = metric.get("axis", "SNMP value")
            unit = metric.get("unit", "")
            plot_title = metric.get("name", "SNMP measurement")

    # ---------------------------------------------------------------
    # Standard GET CSV or generic numeric CSV
    # ---------------------------------------------------------------
    else:
        numeric_rows = []
        for i, row in enumerate(rows):
            raw = str(row.get("numeric_value", "")).strip()
            if not raw:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            numeric_rows.append((i, row, value))

        if not numeric_rows:
            raise RuntimeError("No plottable numeric values were found in this CSV.")

        first_row = numeric_rows[0][1]
        metric = metric_spec_for_oid(
            first_row.get("oid", ""), first_row.get("value_type", "")
        )

        for i, row, value in numeric_rows:
            row_metric = metric_spec_for_oid(
                row.get("oid", ""), row.get("value_type", "")
            )
            transformed = transform_sample_value(row_metric, value)
            if transformed is None:
                continue
            x.append(float(i))
            y.append(transformed)
            metric = row_metric

        axis_label = metric.get("axis", "SNMP value")
        unit = metric.get("unit", "")
        plot_title = metric.get("name", "SNMP measurement")

    # ---------------------------------------------------------------
    # Statistics and graph options
    # ---------------------------------------------------------------
    if not y:
        raise RuntimeError("No values were available to plot.")

    kind = metric.get("kind", "gauge") if metric else "gauge"

    print("\nCapture statistics")
    print("------------------")
    print(f"Metric : {plot_title}")
    print(f"Samples: {len(y)}")

    is_status = kind in ("status_oper", "status_admin")
    if not is_status:
        print(f"Min    : {min(y):.6f} {unit}".rstrip())
        print(f"Max    : {max(y):.6f} {unit}".rstrip())
        print(f"Mean   : {statistics.fmean(y):.6f} {unit}".rstrip())
        print(f"Median : {statistics.median(y):.6f} {unit}".rstrip())
        if len(y) > 1:
            print(f"Std dev: {statistics.stdev(y):.6f} {unit}".rstrip())
    else:
        mapping = STATUS_OPER if kind == "status_oper" else STATUS_ADMIN
        changes = sum(1 for a, b in zip(y, y[1:]) if a != b)
        current = mapping.get(int(y[-1]), f"State {int(y[-1])}")
        print(f"State changes: {changes}")
        print(f"Current state: {current}")

    if repaired_rollovers:
        print(
            f"Corrected {repaired_rollovers} Counter32 rollover interval(s) "
            "during plotting."
        )

    color_options = {
        "1": ("blue", "Blue"),
        "2": ("red", "Red"),
        "3": ("yellow", "Yellow"),
        "4": ("green", "Green"),
        "5": ("hotpink", "Pink"),
        "6": ("black", "Black"),
    }

    print("\nGraph line colors:")
    for number, (_, display_name) in color_options.items():
        print(f"{number}. {display_name}")

    selected_color = next((number for number, (name, _) in color_options.items() if name == color), None)
    color_choice = plot_choice("Choose line color [2 = Red]: ", selected_color, interactive=interactive) or "2"
    line_color, line_color_name = color_options.get(
        color_choice, color_options["2"]
    )

    # Dynamic-range scaling only makes sense for numeric rate/gauge metrics.
    use_symlog = False
    scale_mode_name = "Linear"

    if not is_status and kind not in ("percent", "cpu_average"):
        print("\nY-axis display mode:")
        print("1. Auto     - detailed scale when high/low values differ greatly")
        print("2. Linear   - normal scale")
        print("3. Detailed - symmetric-log; keeps tiny values and spikes visible")
        scale_choice = plot_choice("Choose Y-axis mode [1 = Auto]: ", {"auto": "1", "linear": "2", "symlog": "3"}.get(scale),
                                   interactive=interactive) or "1"

        positive_values = [value for value in y if value > 0]
        smallest_positive = min(positive_values) if positive_values else None
        max_value = max(y)

        dynamic_ratio = (
            max_value / smallest_positive
            if smallest_positive is not None and smallest_positive > 0
            else 1.0
        )

        if scale_choice == "3":
            use_symlog = True
            scale_mode_name = "Detailed (symmetric-log)"
        elif scale_choice == "2":
            use_symlog = False
            scale_mode_name = "Linear"
        else:
            use_symlog = dynamic_ratio >= 100
            scale_mode_name = (
                "Auto → detailed (symmetric-log)"
                if use_symlog
                else "Auto → linear"
            )

    width = min(20.0, max(12.0, 9.0 + len(y) * 0.12))
    fig, ax = plt.subplots(figsize=(width, 7.0))

    ax.plot(
        x,
        y,
        color=line_color,
        linewidth=2.0,
        marker="o",
        markersize=4.0,
        label=plot_title,
    )

    if source_mode == "interval":
        ax.set_xlabel("Elapsed time (seconds; interval midpoint)")
    elif "elapsed_seconds" in fieldnames:
        ax.set_xlabel("Elapsed time (seconds)")
    else:
        ax.set_xlabel("Sample number")

    ax.set_ylabel(axis_label)

    if use_symlog:
        positive_values = [value for value in y if value > 0]
        smallest_positive = min(positive_values) if positive_values else 1.0
        linthresh = max(0.1, min(5.0, smallest_positive * 2.5))
        ax.set_yscale("symlog", linthresh=linthresh, linscale=1.0)
        ax.set_title(f"{plot_title} — detailed dynamic-range view")
        ax.set_ylim(bottom=0)
        ax.grid(True, which="major", alpha=0.35)
        ax.grid(True, which="minor", alpha=0.15)
    else:
        ax.set_title(plot_title)
        ax.grid(True, which="major", alpha=0.3)

        if kind in ("percent", "cpu_average"):
            ax.set_ylim(0, 100)
        elif is_status:
            mapping = STATUS_OPER if kind == "status_oper" else STATUS_ADMIN
            ticks = sorted(mapping.keys())
            ax.set_yticks(ticks)
            ax.set_yticklabels([mapping[t] for t in ticks])
            ax.set_ylim(min(ticks) - 0.4, max(ticks) + 0.4)
        elif y and min(y) >= 0:
            ymax = max(y)
            ax.set_ylim(0, ymax * 1.12 if ymax > 0 else 1)

    if len(x) > 1:
        span = max(x) - min(x)
        x_pad = max(1.0, span * 0.02)
        ax.set_xlim(min(x) - x_pad, max(x) + x_pad)

    # Metric-specific annotations/statistics box.
    if is_status:
        mapping = STATUS_OPER if kind == "status_oper" else STATUS_ADMIN
        changes = sum(1 for a, b in zip(y, y[1:]) if a != b)
        current = mapping.get(int(y[-1]), f"State {int(y[-1])}")
        stats_text = (
            f"Measurements: {len(y)}\n"
            f"State changes: {changes}\n"
            f"Current: {current}\n"
            f"Line: {line_color_name}"
        )
    else:
        min_value = min(y)
        max_value = max(y)
        mean_value = statistics.fmean(y)
        median_value = statistics.median(y)

        min_i = y.index(min_value)
        max_i = y.index(max_value)

        suffix = f" {unit}" if unit else ""
        ax.annotate(
            f"Min {min_value:.3f}{suffix}",
            (x[min_i], y[min_i]),
            xytext=(8, 10),
            textcoords="offset points",
            fontsize=8,
        )
        ax.annotate(
            f"Max {max_value:.3f}{suffix}",
            (x[max_i], y[max_i]),
            xytext=(8, -18),
            textcoords="offset points",
            fontsize=8,
        )

        stats_text = (
            f"Measurements: {len(y)}\n"
            f"Mean: {mean_value:.3f}{suffix}\n"
            f"Median: {median_value:.3f}{suffix}\n"
            f"Min: {min_value:.3f}{suffix}\n"
            f"Max: {max_value:.3f}{suffix}\n"
            f"Line: {line_color_name}\n"
            f"Y scale: {scale_mode_name}"
        )

    ax.text(
        0.015,
        0.975,
        stats_text,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.78},
    )

    ax.legend(loc="best")
    fig.tight_layout()

    safe_metric_name = re.sub(r"[^A-Za-z0-9_-]+", "_", plot_title).strip("_")
    save_default = path.with_name(
        path.stem + "_" + (safe_metric_name or "plot") + ".png"
    )

    return save_plot(plt, fig, save_default, output, show, interactive=interactive)



# ---------------------------------------------------------------------------
# Two-capture comparison plotting
# ---------------------------------------------------------------------------

def load_comparison_series(path, memory_mode=None):
    """
    Load one capture CSV into a normalized comparison series.

    Returns:
        {
            "path": Path,
            "x": [...],
            "y": [...],
            "metric_name": str,
            "metric_kind": str,
            "unit": str,
            "axis": str,
            "source_mode": str,
            "memory_mode": str|None,
        }

    For physical-memory captures, memory_mode can be:
        "used"    -> used physical RAM in GiB
        "percent" -> physical RAM utilization in %
    """
    path = Path(path).expanduser()

    if not path.exists():
        raise RuntimeError(f"File does not exist: {path}")

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        raise RuntimeError(f"CSV contains no data: {path}")

    x = []
    y = []
    metric = None
    source_mode = "raw"

    # ---------------------------------------------------------------
    # Physical-memory capture
    # ---------------------------------------------------------------
    if (
        "elapsed_seconds" in fieldnames
        and "memory_total_gib" in fieldnames
        and "memory_used_gib" in fieldnames
        and any(str(r.get("memory_total_gib", "")).strip() for r in rows)
    ):
        source_mode = "memory"
        memory_mode = memory_mode or "used"

        for row in rows:
            try:
                elapsed = float(row["elapsed_seconds"])
                if memory_mode == "percent":
                    value = float(row["memory_usage_percent"])
                else:
                    value = float(row["memory_used_gib"])
            except (ValueError, TypeError, KeyError):
                continue

            x.append(elapsed)
            y.append(value)

        if not y:
            raise RuntimeError(
                f"No usable physical-memory values found in: {path}"
            )

        if memory_mode == "percent":
            metric = {
                "name": "Physical memory utilization",
                "kind": "percent",
                "axis": "Physical memory utilization (%)",
                "unit": "%",
            }
        else:
            metric = {
                "name": "Used physical memory",
                "kind": "physical_memory",
                "axis": "Used physical memory (GiB)",
                "unit": "GiB",
            }

    # ---------------------------------------------------------------
    # Generic/current interval CSV: rates derived from counters
    # ---------------------------------------------------------------
    elif (
        "rate" in fieldnames
        and "counter_start" in fieldnames
        and "counter_end" in fieldnames
        and "interval_seconds" in fieldnames
    ):
        source_mode = "interval"

        first_oid = rows[0].get("oid", "")
        first_kind = rows[0].get("metric_kind", "")
        metric = metric_spec_for_oid(first_oid)

        if first_kind:
            metric["kind"] = first_kind
        if rows[0].get("metric_name"):
            metric["name"] = rows[0]["metric_name"]
        if rows[0].get("rate_unit"):
            metric["unit"] = rows[0]["rate_unit"]

        for row in rows:
            try:
                start = float(row["interval_start_seconds"])
                end = float(row["interval_end_seconds"])
                seconds = float(row["interval_seconds"])
                counter_start = parse_counter_value(row["counter_start"])
                counter_end = parse_counter_value(row["counter_end"])
                oid = row.get("oid", first_oid)
                value_type = row.get("value_type", "")
            except (ValueError, TypeError, KeyError):
                continue

            row_metric = metric_spec_for_oid(oid, value_type)

            if row.get("metric_kind"):
                row_metric["kind"] = row["metric_kind"]
            if row.get("metric_name"):
                row_metric["name"] = row["metric_name"]
            if row.get("rate_unit"):
                row_metric["unit"] = row["rate_unit"]

            effective_bits = infer_counter_bits(
                value_type, oid, "auto"
            )
            delta = calculate_counter_delta(
                counter_start, counter_end, effective_bits
            )
            rate = counter_rate(row_metric, delta, seconds)

            if rate is None:
                try:
                    rate = float(row["rate"])
                except (ValueError, TypeError, KeyError):
                    continue

            x.append((start + end) / 2.0)
            y.append(rate)
            metric = row_metric

        if not y:
            raise RuntimeError(
                f"No usable counter-rate measurements found in: {path}"
            )

    # ---------------------------------------------------------------
    # Legacy throughput interval CSV
    # ---------------------------------------------------------------
    elif (
        "average_mbps" in fieldnames
        and "interval_seconds" in fieldnames
        and "counter_start" in fieldnames
        and "counter_end" in fieldnames
    ):
        source_mode = "interval"
        metric = {
            "name": "Network throughput",
            "kind": "counter_octets",
            "axis": "Average throughput (Mbps)",
            "unit": "Mbps",
            "counter_bits": 32,
        }

        for row in rows:
            try:
                start = float(row["interval_start_seconds"])
                end = float(row["interval_end_seconds"])
                seconds = float(row["interval_seconds"])
                counter_start = parse_counter_value(row["counter_start"])
                counter_end = parse_counter_value(row["counter_end"])
                oid = row.get("oid", "")
            except (ValueError, TypeError, KeyError):
                continue

            effective_bits = infer_counter_bits("", oid, "auto")
            delta = calculate_counter_delta(
                counter_start, counter_end, effective_bits
            )
            rate = counter_rate(metric, delta, seconds)

            if rate is None:
                continue

            x.append((start + end) / 2.0)
            y.append(rate)

        if not y:
            raise RuntimeError(
                f"No usable throughput measurements found in: {path}"
            )

    # ---------------------------------------------------------------
    # Raw continuous-capture CSV
    # ---------------------------------------------------------------
    elif "elapsed_seconds" in fieldnames and "numeric_value" in fieldnames:
        source_mode = "raw"

        for row in rows:
            try:
                elapsed = float(row["elapsed_seconds"])
            except (ValueError, TypeError, KeyError):
                continue

            oid = row.get("oid", "")
            value_type = row.get("value_type", "")
            row_metric = metric_spec_for_oid(oid, value_type)

            if row.get("metric_kind"):
                row_metric["kind"] = row["metric_kind"]
            if row.get("metric_name"):
                row_metric["name"] = row["metric_name"]
            if row.get("metric_unit"):
                row_metric["unit"] = row["metric_unit"]

            # For a counter raw capture, prefer its already-derived rate
            # values. Unchanged counter rows have blank rate values.
            if is_counter_kind(row_metric.get("kind", "")):
                rate_raw = str(row.get("rate", "")).strip()
                if not rate_raw:
                    continue

                try:
                    plot_value = float(rate_raw)
                except ValueError:
                    continue

                rate_unit = str(row.get("rate_unit", "")).strip()
                if rate_unit:
                    row_metric["unit"] = rate_unit

                if row_metric["kind"] == "counter_octets":
                    row_metric["axis"] = "Average throughput (Mbps)"
                else:
                    row_metric["axis"] = (
                        f"{row_metric.get('name', 'Counter')} "
                        f"({row_metric.get('unit', 'units/s')})"
                    )

            else:
                display_raw = str(row.get("display_value", "")).strip()

                if display_raw:
                    try:
                        plot_value = float(display_raw)
                    except ValueError:
                        continue
                else:
                    raw = str(row.get("numeric_value", "")).strip()
                    if not raw:
                        continue

                    try:
                        numeric_value = float(raw)
                    except ValueError:
                        continue

                    plot_value = transform_sample_value(
                        row_metric, numeric_value
                    )

                if plot_value is None:
                    continue

            x.append(elapsed)
            y.append(float(plot_value))
            metric = row_metric

        if not y:
            raise RuntimeError(
                f"No usable continuous-capture measurements found in: {path}"
            )

    # ---------------------------------------------------------------
    # Generic/GET CSV
    # ---------------------------------------------------------------
    else:
        for i, row in enumerate(rows):
            raw = str(row.get("numeric_value", "")).strip()
            if not raw:
                continue

            try:
                numeric_value = float(raw)
            except ValueError:
                continue

            row_metric = metric_spec_for_oid(
                row.get("oid", ""),
                row.get("value_type", ""),
            )
            plot_value = transform_sample_value(
                row_metric, numeric_value
            )

            if plot_value is None:
                continue

            x.append(float(i))
            y.append(float(plot_value))
            metric = row_metric

        if not y:
            raise RuntimeError(
                f"No plottable numeric values found in: {path}"
            )

    if metric is None:
        raise RuntimeError(f"Could not determine metric type for: {path}")

    # Comparison sessions should begin at x=0 even when interval files
    # naturally use their first update midpoint as the first x coordinate.
    if x:
        first_x = x[0]
        x = [value - first_x for value in x]

    return {
        "path": path,
        "x": x,
        "y": y,
        "metric_name": metric.get("name", "SNMP measurement"),
        "metric_kind": metric.get("kind", "gauge"),
        "unit": metric.get("unit", ""),
        "axis": metric.get("axis", "SNMP value"),
        "source_mode": source_mode,
        "memory_mode": memory_mode,
    }


def comparison_compatible(series_a, series_b):
    """
    Check whether two captures are suitable for a shared Y-axis.

    Same metric kind + same unit is accepted. This intentionally allows,
    for example, ifInOctets and ifOutOctets because both become Mbps.
    """
    kind_a = series_a.get("metric_kind", "")
    kind_b = series_b.get("metric_kind", "")
    unit_a = series_a.get("unit", "")
    unit_b = series_b.get("unit", "")

    if kind_a == kind_b and unit_a == unit_b:
        return True

    # Percentage-like CPU metrics can reasonably share one % axis.
    percentage_kinds = {"percent", "cpu_average"}
    if (
        kind_a in percentage_kinds
        and kind_b in percentage_kinds
        and unit_a == "%"
        and unit_b == "%"
    ):
        return True

    return False


def plot_two_csv_comparison(path_a, path_b, output=None, *, show=None, memory_mode=None,
                            label_first=None, label_second=None, title=None, scale=None, interactive=True):
    """Overlay two compatible capture sessions on one graph."""
    plt = plotting_backend(show is False or (not interactive and show is not True))

    # Check whether either file is a physical-memory capture before loading,
    # because memory can be compared either as GiB used or percentage used.
    def has_memory_columns(path):
        path = Path(path).expanduser()
        if not path.exists():
            return False

        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            required = ("memory_total_gib", "memory_used_gib", "memory_usage_percent")
            if not all(column in fields for column in required):
                return False
            return any(all(str(row.get(column, "")).strip() for column in required) for row in reader)

    memory_a = has_memory_columns(path_a)
    memory_b = has_memory_columns(path_b)

    if memory_a or memory_b:
        if not (memory_a and memory_b):
            raise RuntimeError(
                "One file is a physical-memory capture and the other is not. "
                "Choose two captures containing the same type of measurement."
            )

        print("\nPhysical-memory comparison mode")
        print("-------------------------------")
        print("1. Compare used RAM (GiB)")
        print("2. Compare RAM utilization (%)")
        memory_choice = plot_choice("Choose [1]: ", {"used": "1", "percent": "2"}.get(memory_mode),
                                    interactive=interactive) or "1"
        memory_mode = "percent" if memory_choice == "2" else "used"

    series_a = load_comparison_series(path_a, memory_mode)
    series_b = load_comparison_series(path_b, memory_mode)

    print("\nDetected comparison data")
    print("------------------------")
    print(
        f"File 1: {series_a['metric_name']} "
        f"[{series_a['metric_kind']}, {series_a['unit'] or 'unitless'}]"
    )
    print(
        f"File 2: {series_b['metric_name']} "
        f"[{series_b['metric_kind']}, {series_b['unit'] or 'unitless'}]"
    )

    if not comparison_compatible(series_a, series_b):
        raise RuntimeError(
            "The two CSV files do not contain compatible measurement types. "
            f"File 1 is {series_a['metric_kind']} / "
            f"{series_a['unit'] or 'unitless'}, while File 2 is "
            f"{series_b['metric_kind']} / {series_b['unit'] or 'unitless'}."
        )

    default_label_a = series_a["path"].stem
    default_label_b = series_b["path"].stem

    label_a = (
        plot_choice(f"Label for line 1 [{default_label_a}]: ", label_first, interactive=interactive)
        or default_label_a
    )
    label_b = (
        plot_choice(f"Label for line 2 [{default_label_b}]: ", label_second, interactive=interactive)
        or default_label_b
    )

    color_options = {
        "1": ("blue", "Blue"),
        "2": ("red", "Red"),
        "3": ("yellow", "Yellow"),
        "4": ("green", "Green"),
        "5": ("hotpink", "Pink"),
        "6": ("black", "Black"),
    }

    print("\nLine 1 color:")
    for number, (_, display_name) in color_options.items():
        print(f"{number}. {display_name}")
    choice_a = plot_choice("Choose [1 = Blue]: ", interactive=interactive) or "1"
    color_a, color_a_name = color_options.get(
        choice_a, color_options["1"]
    )

    print("\nLine 2 color:")
    for number, (_, display_name) in color_options.items():
        print(f"{number}. {display_name}")
    choice_b = plot_choice("Choose [2 = Red]: ", interactive=interactive) or "2"
    color_b, color_b_name = color_options.get(
        choice_b, color_options["2"]
    )

    if color_a == color_b:
        print(
            "Both lines use the same color. Different colors are usually "
            "easier to distinguish in a thesis figure."
        )

    x_a = series_a["x"]
    y_a = series_a["y"]
    x_b = series_b["x"]
    y_b = series_b["y"]

    all_y = y_a + y_b
    kind = series_a["metric_kind"]
    unit = series_a["unit"]
    axis_label = series_a["axis"]

    # If the names differ but the data type is compatible, use a neutral title.
    if series_a["metric_name"] == series_b["metric_name"]:
        default_title = f"{series_a['metric_name']} Comparison"
    else:
        default_title = "SNMP Measurement Comparison"

    graph_title = (
        plot_choice(f"Graph title [{default_title}]: ", title, interactive=interactive)
        or default_title
    )

    # Dynamic-range handling across both data series.
    is_status = kind in ("status_oper", "status_admin")
    use_symlog = False
    scale_mode_name = "Linear"

    if not is_status and kind not in (
        "percent", "cpu_average", "physical_memory"
    ):
        print("\nY-axis display mode:")
        print("1. Auto")
        print("2. Linear")
        print("3. Detailed (symmetric-log)")
        scale_choice = plot_choice("Choose [1 = Auto]: ", {"auto": "1", "linear": "2", "symlog": "3"}.get(scale),
                                   interactive=interactive) or "1"

        positive_values = [v for v in all_y if v > 0]
        smallest_positive = (
            min(positive_values) if positive_values else None
        )
        max_value = max(all_y)

        dynamic_ratio = (
            max_value / smallest_positive
            if smallest_positive is not None and smallest_positive > 0
            else 1.0
        )

        if scale_choice == "3":
            use_symlog = True
            scale_mode_name = "Detailed (symmetric-log)"
        elif scale_choice == "2":
            scale_mode_name = "Linear"
        else:
            use_symlog = dynamic_ratio >= 100
            scale_mode_name = (
                "Auto -> detailed (symmetric-log)"
                if use_symlog
                else "Auto -> linear"
            )

    total_points = len(y_a) + len(y_b)
    width = min(20.0, max(12.0, 9.0 + total_points * 0.06))
    fig, ax = plt.subplots(figsize=(width, 7.2))

    ax.plot(
        x_a,
        y_a,
        color=color_a,
        linewidth=2.0,
        marker="o",
        markersize=4.0,
        label=label_a,
    )
    ax.plot(
        x_b,
        y_b,
        color=color_b,
        linewidth=2.0,
        marker="o",
        markersize=4.0,
        label=label_b,
    )

    ax.set_xlabel("Elapsed time from start of each capture (seconds)")
    ax.set_ylabel(axis_label)
    ax.set_title(graph_title)

    if use_symlog:
        positive_values = [v for v in all_y if v > 0]
        smallest_positive = (
            min(positive_values) if positive_values else 1.0
        )
        linthresh = max(
            0.1,
            min(5.0, smallest_positive * 2.5),
        )
        ax.set_yscale(
            "symlog",
            linthresh=linthresh,
            linscale=1.0,
        )
        ax.set_ylim(bottom=0)
        ax.grid(True, which="major", alpha=0.35)
        ax.grid(True, which="minor", alpha=0.15)
    else:
        ax.grid(True, which="major", alpha=0.3)

        if kind in ("percent", "cpu_average"):
            ax.set_ylim(0, 100)
        elif memory_mode == "percent":
            ax.set_ylim(0, 100)
        elif memory_mode == "used":
            # A memory comparison should show both sessions against the
            # same zero baseline. The upper bound is based on observed data
            # because the two systems/sessions may have different capacities.
            ymax = max(all_y)
            ax.set_ylim(0, ymax * 1.12 if ymax > 0 else 1)
        elif is_status:
            mapping = (
                STATUS_OPER if kind == "status_oper"
                else STATUS_ADMIN
            )
            ticks = sorted(mapping.keys())
            ax.set_yticks(ticks)
            ax.set_yticklabels(
                [mapping[t] for t in ticks]
            )
            ax.set_ylim(
                min(ticks) - 0.4,
                max(ticks) + 0.4,
            )
        elif min(all_y) >= 0:
            ymax = max(all_y)
            ax.set_ylim(0, ymax * 1.12 if ymax > 0 else 1)

    max_x = max(
        max(x_a) if x_a else 0,
        max(x_b) if x_b else 0,
    )
    ax.set_xlim(
        0,
        max_x * 1.02 if max_x > 0 else 1,
    )

    suffix = f" {unit}" if unit else ""

    if is_status:
        stats_a = (
            f"{label_a}: {len(y_a)} samples"
        )
        stats_b = (
            f"{label_b}: {len(y_b)} samples"
        )
    else:
        mean_a = statistics.fmean(y_a)
        mean_b = statistics.fmean(y_b)

        stats_a = (
            f"{label_a}: n={len(y_a)}, "
            f"mean={mean_a:.3f}{suffix}, "
            f"min={min(y_a):.3f}, max={max(y_a):.3f}"
        )
        stats_b = (
            f"{label_b}: n={len(y_b)}, "
            f"mean={mean_b:.3f}{suffix}, "
            f"min={min(y_b):.3f}, max={max(y_b):.3f}"
        )

    stats_text = (
        f"{stats_a}\n"
        f"{stats_b}\n"
        f"Line 1: {color_a_name} | Line 2: {color_b_name}\n"
        f"Y scale: {scale_mode_name}"
    )

    ax.text(
        0.015,
        0.975,
        stats_text,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={
            "boxstyle": "round",
            "facecolor": "white",
            "alpha": 0.78,
        },
    )

    ax.legend(loc="best")
    fig.tight_layout()

    default_output = series_a["path"].with_name(
        f"{series_a['path'].stem}_VS_{series_b['path'].stem}.png"
    )
    return save_plot(plt, fig, default_output, output, show, interactive=interactive, comparison=True)


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

def choose_output_mode():
    """Choose terminal output, CSV output, or both for GET/WALK."""
    print("\nOutput mode")
    print("-----------")
    print("1. Display in terminal only")
    print("2. Save to CSV only")
    print("3. Display in terminal + save to CSV")

    choice = input("Choose [3]: ").strip() or "3"

    if choice == "1":
        return True, False
    if choice == "2":
        return False, True
    return True, True


def menu_get():
    target, version, community = prompt_connection()

    print("\nOID selection")
    print("-------------")
    print("1. Choose from common OID catalog")
    print("2. Enter OID manually")
    selection = input("Choose [1]: ").strip() or "1"

    if selection == "2":
        oid = input("OID to GET: ").strip()
    else:
        oid = choose_oid_from_catalog()

    show_terminal, save_csv = choose_output_mode()

    filename = None
    if save_csv:
        filename = ensure_csv_suffix(
            input("CSV filename [snmp_get.csv]: ").strip() or "snmp_get.csv"
        )

    # Special physical-memory summary mode
    if is_physical_memory_auto_oid(oid):
        memory = get_physical_memory_snapshot(
            target, version, community
        )

        if show_terminal:
            print("\nPhysical Memory Summary")
            print("-----------------------")
            print(f"Storage index      : {memory['index']}")
            print(f"Description        : {memory['description']}")
            print(f"Storage type       : {memory['storage_type']}")
            print(f"Allocation unit    : {memory['allocation_unit_bytes']} bytes")
            print(f"Total physical RAM : {memory['total_gib']:.3f} GiB")
            print(f"Used physical RAM  : {memory['used_gib']:.3f} GiB")
            print(f"Free physical RAM  : {memory['free_gib']:.3f} GiB")
            print(f"RAM utilization    : {memory['usage_percent']:.2f}%")
            print(f"RAM free           : {memory['free_percent']:.2f}%")
            print(
                "RAM technology      : HOST-RESOURCES-MIB identifies this as RAM; "
                "DDR3/DDR4/DDR5 is not exposed by this standard OID."
            )

        if save_csv:
            # Store the main percentage result using the standard GET CSV format.
            pseudo_record = (
                PHYSICAL_MEMORY_ROOT_OID,
                "PhysicalMemoryAggregate",
                f"{memory['usage_percent']:.3f}",
                memory["usage_percent"],
            )
            save_standard_result(
                filename,
                target,
                "GET-PHYSICAL-MEMORY",
                [pseudo_record]
            )
            print(f"\nSaved summary value to: {filename}")

        return

    # Special aggregate CPU mode
    if is_cpu_average_oid(oid):
        average, cores, minimum, maximum = snmp_cpu_average(
            target, version, community
        )

        pseudo_record = (
            CPU_LOAD_ROOT_OID,
            "AggregateGauge",
            f"{average:.3f}",
            average,
        )

        if save_csv:
            save_standard_result(
                filename, target, "GET-CPU-AVERAGE", [pseudo_record]
            )

        if show_terminal:
            print("\nResponse:")
            print(f"OID         : {CPU_LOAD_ROOT_OID}")
            print("Metric      : Average CPU load")
            print(f"Logical CPUs: {len(cores)}")
            print(f"Average     : {average:.2f}%")
            print(f"Lowest CPU  : {minimum:.2f}%")
            print(f"Highest CPU : {maximum:.2f}%")
            print("Per-CPU values:")
            for core in cores:
                print(
                    f"  index {core['index']}: "
                    f"{core['load_percent']:.1f}%"
                )

        if save_csv:
            print(f"\nSaved to: {filename}")
        return

    record = snmp_get(target, version, community, oid)
    metric = metric_spec_for_oid(record[0], record[1])
    transformed = transform_sample_value(metric, record[3])

    if show_terminal:
        print("\nResponse:")
        print(f"OID    : {record[0]}")
        print(f"Metric : {metric['name']}")
        print(f"Type   : {record[1]}")
        print(f"Value  : {record[2]}")
        if transformed is not None and not is_counter_kind(metric["kind"]):
            print(f"Display: {format_metric_value(metric, transformed)}")

    if save_csv:
        save_standard_result(filename, target, "GET", [record])
        print(f"\nSaved to: {filename}")

def menu_walk():
    target, version, community = prompt_connection()

    print("\nWALK root selection")
    print("-------------------")
    print("1. Enter root OID manually")
    print("2. Interface descriptions (ifDescr)")
    print("3. Interface operational status (ifOperStatus)")
    print("4. CPU processor-load table (hrProcessorLoad)")
    print("5. System information group")
    print("6. Storage descriptions (find Physical Memory index)")
    print("7. All device metrics / clean MIB tree")
    print("8. Running process table")
    print("9. Filesystem capacity table")
    print("10. Disk I/O table")

    selection = input("Choose [1]: ").strip() or "1"

    if selection == "2":
        oid = ".1.3.6.1.2.1.2.2.1.2"
    elif selection == "3":
        oid = ".1.3.6.1.2.1.2.2.1.8"
    elif selection == "4":
        oid = CPU_LOAD_ROOT_OID
    elif selection == "5":
        oid = ".1.3.6.1.2.1.1"
    elif selection == "6":
        oid = HR_STORAGE_DESCR_ROOT
    elif selection == "7":
        oid = ".1.3.6.1"
    elif selection == "8":
        oid = mib.oid_string(mib.PROCESS_TABLE)
    elif selection == "9":
        oid = mib.oid_string(mib.FILESYSTEM)
    elif selection == "10":
        oid = mib.oid_string(mib.DISKIO)
    else:
        oid = (
            input("Root OID to WALK [.1.3.6.1.2.1]: ").strip()
            or ".1.3.6.1.2.1"
        )

    show_terminal, save_csv = choose_output_mode()

    filename = None
    if save_csv:
        filename = ensure_csv_suffix(
            input("CSV filename [snmp_walk.csv]: ").strip()
            or "snmp_walk.csv"
        )

    records = snmp_walk(target, version, community, oid)

    if show_terminal:
        print("\nSNMP WALK response")
        print("------------------")
        if not records:
            print("No OID/value pairs were returned.")
        else:
            for returned_oid, value_type, value, numeric_value in records:
                if value_type:
                    print(f"{returned_oid} = {value_type}: {value}")
                else:
                    print(f"{returned_oid} = {value}")
            print(f"\nTotal OID/value pairs: {len(records)}")

    if save_csv:
        save_standard_result(filename, target, "WALK", records)
        print(f"\nSaved {len(records)} OID/value pair(s) to: {filename}")

def menu_set():
    print("\nWARNING: SNMP SET modifies the remote device.")
    target, version, community = prompt_connection()
    oid = input("OID to SET: ").strip()

    print("\nCommon Net-SNMP SET types:")
    print("  i = INTEGER")
    print("  u = Unsigned/Gauge")
    print("  s = STRING")
    print("  x = HEX STRING")
    print("  t = TIMETICKS")
    print("  a = IP ADDRESS")
    print("  o = OBJECT ID")

    data_type = input("Data type: ").strip()
    value = input("New value: ").strip()

    confirm = input(
        f"\nSend SET to {target}: {oid} ({data_type}) = {value}? [y/N]: "
    ).strip().lower()

    if confirm != "y":
        print("SET cancelled.")
        return

    record = snmp_set(target, version, community, oid, data_type, value)
    filename = ensure_csv_suffix(
        input("CSV filename [snmp_set.csv]: ").strip() or "snmp_set.csv"
    )
    save_standard_result(filename, target, "SET", [record])

    print("\nResponse:")
    print(f"{record[0]} = {record[2]}")
    print(f"Saved to: {filename}")


def menu_capture():
    target, version, community = prompt_connection()

    print("\nOID selection")
    print("-------------")
    print("1. Choose from common OID catalog")
    print("2. Enter OID manually")
    selection = input("Choose [1]: ").strip() or "1"

    if selection == "2":
        oid = input("OID to poll: ").strip()
    else:
        oid = choose_oid_from_catalog()

    known_metric = metric_spec_for_oid(oid)
    print(f"\nExpected metric: {known_metric['name']}")

    interval_raw = input(f"Polling interval in seconds [{_MANAGER_CONFIG.interval:g}]: ").strip() or str(_MANAGER_CONFIG.interval)
    try:
        interval = positive_seconds(interval_raw)
    except ValueError:
        print("Invalid interval; using 1 second.")
        interval = 1.0

    filename = ensure_csv_suffix(
        input("Capture CSV filename [capture.csv]: ").strip() or "capture.csv"
    )

    counter_bits = "auto"
    if is_counter_kind(known_metric["kind"]):
        bits_raw = input(
            "Counter width [auto/32/64] [auto]: "
        ).strip().lower() or "auto"

        if bits_raw == "64":
            counter_bits = 64
        elif bits_raw == "32":
            counter_bits = 32
        else:
            counter_bits = "auto"

        print(
            "Counter width mode:",
            "automatic detection" if counter_bits == "auto"
            else f"{counter_bits}-bit fallback"
        )
    else:
        print("This is not a cumulative counter; every successful poll will be saved as a sample.")

    duration_raw = input(
        "Duration in seconds, or Enter to run until Ctrl+C: "
    ).strip()

    duration = None
    if duration_raw:
        try:
            duration = positive_seconds(duration_raw)
        except ValueError:
            print("Invalid duration; capture will run until Ctrl+C.")

    continuous_capture(
        target=target,
        version=version,
        community=community,
        oid=oid,
        interval=interval,
        output_file=filename,
        counter_bits=counter_bits,
        duration=duration,
    )

def menu_plot():
    filename = input("CSV file to plot: ").strip()
    if not filename:
        print("No filename supplied.")
        return

    plot_csv(Path(filename).expanduser())


def menu_compare_plots():
    print("\nTwo-capture comparison")
    print("----------------------")
    print(
        "Choose two CSV captures containing the same type of metric. "
        "Both sessions will be aligned to elapsed time 0."
    )

    first = input("First CSV file: ").strip()
    if not first:
        print("No first filename supplied.")
        return

    second = input("Second CSV file: ").strip()
    if not second:
        print("No second filename supplied.")
        return

    plot_two_csv_comparison(
        Path(first).expanduser(),
        Path(second).expanduser(),
    )


def interactive_main():
    while True:
        print("\n" + "=" * 58)
        print(" SNMP DATA COLLECTOR")
        print("=" * 58)
        print("1. SNMP GET -> save to CSV")
        print("2. SNMP WALK -> save OID/value pairs to CSV")
        print("3. SNMP SET -> optionally save result to CSV")
        print("4. Continuous OID capture -> CSV + rate calculation")
        print("5. Plot/statistics from CSV")
        print("6. Show common OID reference")
        print("7. Compare two CSV captures -> one graph / two lines")
        print("8. Configure RSA-encrypted traffic")
        print("9. Generate manager RSA keys")
        print("10. Show clean MIB structure tree")
        print("11. Browse named device metrics")
        print("help. Describe commands and menu options")
        print("0. Exit")

        choice = input("\nChoose an option: ").strip()

        try:
            required_tools = {"1": ("snmpget", "snmpwalk"), "2": ("snmpwalk",), "3": ("snmpset",),
                              "4": ("snmpget", "snmpwalk"), "11": ("snmpwalk",)}
            if choice in required_tools and not check_dependencies(required_tools[choice]):
                continue
            if choice == "1":
                menu_get()
            elif choice == "2":
                menu_walk()
            elif choice == "3":
                menu_set()
            elif choice == "4":
                menu_capture()
            elif choice == "5":
                menu_plot()
            elif choice == "6":
                print_oid_reference()
            elif choice == "7":
                menu_compare_plots()
            elif choice == "8":
                filename = input("Encryption profile JSON (Enter disables encryption): ").strip()
                configure_encryption(Path(filename).expanduser() if filename else None)
                print("Traffic encryption:", "enabled" if _TRAFFIC_CIPHER else "disabled")
            elif choice == "9":
                from snmp_crypto import generate_key_bundle
                directory = input("RSA key output directory [keys]: ").strip() or "keys"
                paths = generate_key_bundle(directory)
                for name, path in paths.items():
                    print(f"{name}: {path}")
            elif choice == "10":
                print(mib.render_mib_tree())
            elif choice == "11":
                target, version, community = prompt_connection()
                group = input("Metric group (Enter = all): ").strip() or None
                if group and group not in mib.COLLECTION_GROUPS and group != "control":
                    raise ValueError("Unknown metric group.")
                browse_device(target, version, community, group)
            elif choice.lower() in ("help", "h", "?"):
                print("\nMenu: 1=GET, 2=WALK, 3=SET, 4=capture, 5=plot, 6=OID catalog, 7=compare,")
                print("8=encryption profile, 9=RSA keys, 10=MIB tree, 11=browse named metrics, 0=exit.")
                show_command_help(build_manager_parser())
            elif choice == "0":
                print("Goodbye.")
                break
            else:
                print("Invalid choice.")
        except RuntimeError as exc:
            print(f"\nSNMP ERROR: {exc}")
        except PermissionError as exc:
            print(f"\nFILE ERROR: {exc}")
        except EOFError:
            raise
        except Exception as exc:
            print(f"\nERROR: {exc}")


def browse_device(target, version="2c", community="public", group=None, root=None, output=None):
    records = {}
    roots = (tuple(map(int, root.strip().lstrip(".").split("."))),) if root else mib.walk_roots(group)
    for subtree in roots:
        for record in snmp_walk(target, version, community, mib.oid_string(subtree)):
            entry = mib.get_mib_object(record[0])
            if entry is None or (group and entry.group != group and collection_group_for_oid(record[0]) != group):
                continue
            records[record[0]] = record
    ordered = sorted(records.values(), key=lambda record: tuple(map(int, normalize_oid(record[0]).split("."))))
    for oid, value_type, value, numeric in ordered:
        entry = mib.get_mib_object(oid)
        specification = metric_spec_for_oid(oid, value_type)
        converted = transform_sample_value(specification, numeric)
        if is_counter_kind(specification["kind"]):
            displayed = f"{numeric} cumulative ({value_type})"
        elif converted is not None:
            displayed = format_metric_value(specification, converted)
        else:
            displayed = value
        print(f"{entry.name}: {displayed}\n  {oid} -- {entry.description}")
    if output:
        save_standard_result(ensure_csv_suffix(output), target, "BROWSE", ordered)
    print(f"\nDisplayed {len(ordered)} available MIB instances.")
    return ordered


def add_manager_options(parser, *, inherited=False):
    default = argparse.SUPPRESS if inherited else None
    settings = parser.add_mutually_exclusive_group()
    settings.add_argument("--config", type=Path, default=default, help="Manager JSON profile; CLI flags override its defaults")
    settings.add_argument("--no-config", action="store_true", default=argparse.SUPPRESS if inherited else False,
                          help="Ignore automatic JSON/environment configuration")
    banner = parser.add_mutually_exclusive_group()
    banner.add_argument("--banner", dest="banner", action="store_true", default=default, help="Display the Hookah startup banner")
    banner.add_argument("--no-banner", dest="banner", action="store_false", default=default, help="Suppress the startup banner")
    parser.add_argument("--snmp-bin", type=Path, default=default, help="Directory containing Net-SNMP tools (useful on Windows)")


def retry_count(value):
    number = int(value)
    if not 0 <= number <= 10:
        raise ValueError("Use between 0 and 10 retries.")
    return number


def numeric_oid(value):
    if not re.fullmatch(r"\.?\d+(?:\.\d+)+", value):
        raise argparse.ArgumentTypeError("Use a numeric OID, for example .1.3.6.1.2.1.1.1.0.")
    return value


def add_connection_options(command):
    command.add_argument("--target", help="Device IPv4/hostname or udp:host:port; defaults to the profile target")
    command.add_argument("--version", choices=("1", "2c"), help="SNMP protocol version (default: profile or 2c)")
    command.add_argument("--community", help="Read/write community (default: profile or public)")
    command.add_argument("--timeout", type=positive_seconds, help="SNMP request timeout in seconds (default: 2)")
    command.add_argument("--retries", type=retry_count, help="SNMP retry count, 0 through 10 (default: 1)")
    command.add_argument("--encryption-config", type=Path, help="Optional RSA-encrypted traffic profile")


def add_plot_options(command):
    command.add_argument("--output", type=Path, help="PNG destination; parent directories are created automatically")
    display = command.add_mutually_exclusive_group()
    display.add_argument("--show", dest="show", action="store_true", help="Also open a graphical plot window")
    display.add_argument("--no-show", dest="show", action="store_false", help="Save only (the default); works on headless Linux")
    command.set_defaults(show=False)
    command.add_argument("--scale", choices=("auto", "linear", "symlog"), default="auto", help="Numeric Y-axis display mode")


def build_manager_parser():
    parser = argparse.ArgumentParser(
        prog=os.environ.get("HOOKAH_PROGRAM_NAME"),
        description="Hookah Manager: Linux/Windows SNMP CLI and interactive menu.",
        epilog="No arguments opens the menu on a terminal; redirected input prints help. "
               "Use 'interactive' explicitly for a scripted menu. Exit codes: 0=success, 1=runtime error, 2=usage error, 130=Ctrl+C.",
    )
    parser.add_argument("--version", action="version", version=f"Hookah Manager {__version__}")
    add_manager_options(parser)
    commands = parser.add_subparsers(dest="command")
    help_command = commands.add_parser("help", help="List commands/options and explain a specific topic")
    help_command.add_argument("topic", nargs="*", help="Examples: capture, mib, keys generate")
    commands.add_parser("catalog", help="List every numbered OID option, description, and platform availability")
    tree = commands.add_parser("mib", help="Display the clean shared MIB tree; optionally query live values")
    tree.add_argument("--group", choices=tuple(mib.COLLECTION_GROUPS) + ("control",), help="Filter the MIB by collection group")
    tree.add_argument("--root", type=numeric_oid, help="Filter the MIB by numeric OID subtree")
    browse = commands.add_parser("browse", help="Query available device metrics with readable names and units")
    browse.add_argument("--group", choices=tuple(mib.COLLECTION_GROUPS) + ("control",), help="Select a metric family")
    browse.add_argument("--root", type=numeric_oid, help="Select a numeric subtree to WALK")
    for command in (tree, browse):
        add_connection_options(command)
        command.add_argument("--output", type=Path, help="Save queried values as CSV")
    plot = commands.add_parser("plot", help="Plot numeric CSV values and print capture statistics")
    plot.add_argument("csv", type=Path)
    add_plot_options(plot)
    plot.add_argument("--color", choices=("blue", "red", "yellow", "green", "hotpink", "black"), default="red")
    plot.add_argument("--memory-mode", choices=("used", "percent", "both"), default="used")
    compare = commands.add_parser("compare", help="Overlay two compatible CSV capture sessions")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    add_plot_options(compare)
    compare.add_argument("--memory-mode", choices=("used", "percent"), default="used")
    compare.add_argument("--label-first", help="Legend label for the first capture")
    compare.add_argument("--label-second", help="Legend label for the second capture")
    compare.add_argument("--title", help="Graph title")
    commands.add_parser("interactive", help="Open the original menu, including with piped input")
    commands.add_parser("doctor", help="Check Python dependencies and Net-SNMP tool paths without contacting a device")
    configuration = commands.add_parser("config", help="Initialize or inspect a Manager JSON profile")
    config_actions = configuration.add_subparsers(dest="config_action", required=True)
    for action in ("init", "show"):
        command = config_actions.add_parser(action, help="Create a new profile" if action == "init" else "Show resolved profile defaults")
        add_manager_options(command, inherited=True)
    keys = commands.add_parser("keys", help="Generate RSA keys and an encryption profile on the manager")
    key_actions = keys.add_subparsers(dest="key_action", required=True)
    generate = key_actions.add_parser("generate")
    generate.add_argument("--directory", type=Path, default=Path("keys"))
    generate.add_argument("--bits", type=int, choices=(2048, 3072, 4096), default=3072)
    for operation in ("get", "walk", "set", "capture"):
        descriptions = {"get": "Read one OID value", "walk": "Discover all OID values beneath a subtree",
                        "set": "Write an allowed OID using its SNMP type", "capture": "Poll a metric, calculate rates, and save CSV"}
        command = commands.add_parser(operation, help=descriptions[operation], description=descriptions[operation])
        add_connection_options(command)
        command.add_argument("--oid", required=True, type=numeric_oid, help="Numeric instance OID for GET/SET/capture, or subtree root for WALK")
        if operation != "capture":
            command.add_argument("--output", type=Path, help="Optionally append results to this CSV")
        if operation == "set":
            command.add_argument("--type", dest="data_type", required=True, help="Net-SNMP type, e.g. s or i")
            command.add_argument("--value", required=True, help="New value to send in the SET request")
        if operation == "capture":
            command.add_argument("--output", type=Path, required=True, help="Raw sample CSV; counter rates also create an _intervals.csv file")
            command.add_argument("--interval", type=positive_seconds, help="Polling interval in seconds (default: profile or 1)")
            command.add_argument("--duration", type=positive_seconds, help="Capture duration in seconds; omitted means run until Ctrl+C")
            command.add_argument("--counter-bits", choices=("auto", "32", "64"), help="Counter-width fallback for wraparound calculations")
            command.add_argument("--sync-agent", action="store_true",
                                 help="Follow this agent's collection interval, including live changes")
    for command in commands.choices.values():
        add_manager_options(command, inherited=True)
    return parser


def manager_config_for_args(args):
    configuration = load_manager_config(args.config, use_defaults=not args.no_config)
    updates = {}
    for name in configuration.document():
        value = getattr(args, name, None)
        if value is not None:
            updates[name] = value.expanduser().resolve() if isinstance(value, Path) else value
    return replace(configuration, **updates)


@contextlib.contextmanager
def manager_session(configuration):
    global _MANAGER_CONFIG, _TRAFFIC_CIPHER
    previous = _MANAGER_CONFIG, _TRAFFIC_CIPHER
    _MANAGER_CONFIG = configuration
    try:
        yield
    finally:
        _MANAGER_CONFIG, _TRAFFIC_CIPHER = previous


def doctor():
    print(f"Hookah Manager {__version__}\nPython: {sys.version.split()[0]} ({sys.executable})")
    ready = True
    for name in ("snmpget", "snmpwalk", "snmpset"):
        path = snmp_tool(name)
        print(f"{name}: {path or 'MISSING'}")
        ready = ready and path is not None
    for name in ("matplotlib", "cryptography"):
        available = importlib.util.find_spec(name) is not None
        print(f"{name}: {'available' if available else 'MISSING (install requirements-manager.txt)'}")
        ready = ready and available
    if not ready:
        check_dependencies()
    return 0 if ready else 1


def command_line_main(argv):
    parser = build_manager_parser()
    args = parser.parse_args(argv)
    if args.config is not None and args.no_config:
        parser.error("--config and --no-config cannot be used together.")
    if args.command is None:
        parser.print_help()
        return 0
    if args.banner is True or (args.command == "interactive" and args.banner is not False):
        print_banner()
    if args.command == "help":
        try:
            show_command_help(parser, args.topic)
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if args.command == "catalog":
        print_oid_reference()
        return 0
    if args.command == "mib":
        try:
            print(mib.render_mib_tree(args.group, args.root))
        except ValueError as exc:
            parser.error(str(exc))
        if not args.target:
            return 0
    if args.command in ("plot", "compare"):
        try:
            if args.command == "plot":
                plot_csv(args.csv, args.output, show=args.show, color=args.color, scale=args.scale,
                         memory_mode=args.memory_mode, interactive=False)
            else:
                plot_two_csv_comparison(args.first, args.second, args.output, show=args.show, scale=args.scale,
                                        memory_mode=args.memory_mode, label_first=args.label_first,
                                        label_second=args.label_second, title=args.title, interactive=False)
        except BrokenPipeError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "keys":
        from snmp_crypto import generate_key_bundle
        try:
            paths = generate_key_bundle(args.directory, args.bits)
            for name, path in paths.items():
                print(f"{name}: {path}")
        except BrokenPipeError:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        if args.command == "config" and args.config_action == "init":
            print(json.dumps(initialize_manager_config(args.config or Path(CONFIG_NAME)), indent=2))
            return 0
        configuration = manager_config_for_args(args)
        if args.command == "config":
            print(json.dumps(configuration.document(), indent=2))
            return 0
        with manager_session(configuration):
            if args.command == "doctor":
                return doctor()
            if args.command == "interactive":
                configure_encryption(configuration.encryption_config)
                interactive_main()
                return 0
            if not configuration.target:
                parser.error("Supply --target or set target in the Manager JSON profile.")
            tools = {"get": ("snmpget",), "walk": ("snmpwalk",), "set": ("snmpset",),
                     "capture": ("snmpget",), "browse": ("snmpwalk",), "mib": ("snmpwalk",)}[args.command]
            aggregate = is_cpu_average_oid(getattr(args, "oid", "")) or is_physical_memory_auto_oid(getattr(args, "oid", ""))
            if aggregate and args.command in ("get", "capture"):
                tools = ("snmpget", "snmpwalk")
            if not check_dependencies(tools):
                return 1
            configure_encryption(configuration.encryption_config)
            target, version, community = configuration.target, configuration.version, configuration.community
            if args.command in ("browse", "mib"):
                browse_device(target, version, community, args.group, args.root, args.output)
                return 0
            if args.command == "capture":
                return continuous_capture(
                    target, version, community, args.oid, configuration.interval, ensure_csv_suffix(args.output),
                    counter_bits="auto" if configuration.counter_bits == "auto" else int(configuration.counter_bits),
                    duration=args.duration, sync_agent=args.sync_agent,
                )
            if args.command == "get":
                if is_cpu_average_oid(args.oid):
                    average, _, _, _ = snmp_cpu_average(target, version, community)
                    records = [(args.oid, "AggregateGauge", f"{average:.3f}", average)]
                elif is_physical_memory_auto_oid(args.oid):
                    memory = get_physical_memory_snapshot(target, version, community)
                    records = [(args.oid, "PhysicalMemoryAggregate", f"{memory['usage_percent']:.3f}", memory["usage_percent"])]
                else:
                    records = [snmp_get(target, version, community, args.oid)]
            elif args.command == "walk":
                records = snmp_walk(target, version, community, args.oid)
            else:
                records = [snmp_set(target, version, community, args.oid, args.data_type, args.value)]
            for oid, value_type, value, _ in records:
                print(f"{oid} = {value_type + ': ' if value_type else ''}{value}")
            if args.output:
                save_standard_result(ensure_csv_suffix(args.output), target, args.command.upper(), records)
    except BrokenPipeError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and sys.stdin.isatty():
        arguments = ["interactive"]
    try:
        status = command_line_main(arguments)
        sys.stdout.flush()
        return status
    except EOFError:
        print("\nGoodbye.")
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Behave quietly when a Linux consumer closes a pipe early.
        try:
            descriptor = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(descriptor, sys.stdout.fileno())
            finally:
                os.close(descriptor)
        except (OSError, ValueError, io.UnsupportedOperation):
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
