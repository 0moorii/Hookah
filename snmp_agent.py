#!/usr/bin/env python3
"""Live SNMPv1/v2c host agent for snmp_data_collector_v12.py.

The manager sends GET/WALK requests to this device's UDP listening port. This
agent answers with locally collected system, interface, CPU, and RAM metrics.
See README-agent.md for configuration, OIDs, and service installation.
"""

import argparse
import asyncio
import bisect
import contextlib
import ipaddress
import json
import logging
import math
import mmap
import os
import platform
import signal
import socket
import sys
import tempfile
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import snmp_mib as mib
from snmp_cli_help import show_command_help

from snmp_crypto import (
    CryptoProtocolError, EncryptionSettings, EncryptedSnmpProxy,
    MAX_AGE_SECONDS, REQUEST, RESPONSE, RsaEnvelope, load_encryption_settings, snmp_request_id,
)

from snmp_protocol import (
    COLLECTION_CONFIG_OID, COLLECTION_GROUPS, COLLECTION_TABLE,
    EXAMPLE_ROOT, collection_metadata_oid,
)

try:
    import psutil
    from pysnmp.carrier.asyncio.dgram import udp
    from pysnmp.entity import config, engine
    from pysnmp.entity.rfc3413 import cmdrsp, context
    from pysnmp.proto import rfc1902, rfc1905
    from pysnmp.smi import error, instrum
except ImportError as exc:
    raise SystemExit(
        f"Missing agent dependency: {exc}. "
        "Install with: python -m pip install -r requirements-agent.txt"
    ) from exc


LOGGER = logging.getLogger("snmp_agent")
SYSTEM = (1, 3, 6, 1, 2, 1, 1)
IF_TABLE = (1, 3, 6, 1, 2, 1, 2, 2, 1)
IFX_TABLE = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)
CPU_LOAD = (1, 3, 6, 1, 2, 1, 25, 3, 3, 1, 2)
STORAGE = (1, 3, 6, 1, 2, 1, 25, 2, 3, 1)
RAM_TYPE = (1, 3, 6, 1, 2, 1, 25, 2, 1, 2)
UCD = (1, 3, 6, 1, 4, 1, 2021)
PACKET_TABLE = EXAMPLE_ROOT + (2, 1)
SYSTEM_WRITABLE = {
    SYSTEM + (4, 0): "sys_contact",
    SYSTEM + (5, 0): "sys_name",
    SYSTEM + (6, 0): "sys_location",
}
INTEGER_MAX = (1 << 31) - 1
DEFAULT_CONFIG_FILE = Path("snmp_agent_config.json")
CONFIG_CHECK_INTERVAL = 0.2


def positive_seconds(value):
    """Accept seconds as a number, without duration suffixes or unit conversion."""
    if isinstance(value, bool):
        raise ValueError("Intervals must be finite positive numbers in seconds.")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Intervals must be numeric seconds, e.g. 1, 10, or 120.") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Intervals must be finite positive numbers in seconds.")
    return seconds


def validate_display_string(name, value):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    if len(value.encode("utf-8")) > 255:
        raise ValueError(f"{name} must fit in 255 UTF-8 bytes.")


@dataclass(frozen=True)
class AgentConfig:
    listen_address: str = "0.0.0.0"
    port: int = 161
    read_community: str = "public"
    write_community: str | None = None
    allowed_managers: tuple[str, ...] = ("127.0.0.1/32",)
    sample_interval: float = 1.0
    collection_intervals: dict[str, float] = field(default_factory=dict)
    sys_contact: str = ""
    sys_name: str | None = None
    sys_location: str = ""
    state_file: Path | None = None
    encryption: dict = field(default_factory=lambda: {
        "enabled": False, "private_key_file": None, "peer_public_key_file": None,
    })
    manager_networks: tuple = field(init=False, repr=False)

    def __post_init__(self):
        ipaddress.IPv4Address(self.listen_address)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer between 1 and 65535.")
        for name in ("read_community", "write_community"):
            value = getattr(self, name)
            if name == "write_community" and value is None:
                continue
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string.")
            if len(value.encode("utf-8")) > 255:
                raise ValueError(f"{name} must fit in 255 UTF-8 bytes.")
        if self.read_community == self.write_community:
            raise ValueError("Read and write communities must be different.")
        if not isinstance(self.allowed_managers, (list, tuple)) or not self.allowed_managers:
            raise ValueError("allowed_managers must contain at least one IPv4 address or CIDR.")
        networks = []
        for address in self.allowed_managers:
            if not isinstance(address, str):
                raise ValueError("allowed_managers entries must be IPv4 address/CIDR strings.")
            networks.append(ipaddress.IPv4Network(address, strict=False))
        object.__setattr__(self, "allowed_managers", tuple(self.allowed_managers))
        object.__setattr__(self, "manager_networks", tuple(networks))
        if not isinstance(self.sample_interval, (int, float)):
            raise ValueError("sample_interval must be a numeric value in seconds.")
        positive_seconds(self.sample_interval)
        if not isinstance(self.collection_intervals, dict):
            raise ValueError("collection_intervals must be an object of metric names and seconds.")
        if set(self.collection_intervals) - set(COLLECTION_GROUPS):
            raise ValueError("Unknown collection metric; choose: " + ", ".join(COLLECTION_GROUPS))
        for group, seconds in self.collection_intervals.items():
            if not isinstance(seconds, (int, float)):
                raise ValueError(f"collection_intervals.{group} must be numeric seconds.")
            positive_seconds(seconds)
        object.__setattr__(self, "collection_intervals", dict(self.collection_intervals))
        for name in ("sys_contact", "sys_name", "sys_location"):
            value = getattr(self, name)
            if name == "sys_name" and value is None:
                continue
            validate_display_string(name, value)
        if self.state_file is not None and not isinstance(self.state_file, Path):
            raise ValueError("state_file must be a path or null.")
        EncryptionSettings.from_document(self.encryption)
        object.__setattr__(self, "encryption", dict(self.encryption))

    def allows_manager(self, address):
        try:
            source = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            return False
        return any(source in network for network in self.manager_networks)

    def interval_for(self, group):
        if group not in COLLECTION_GROUPS:
            raise ValueError(f"Unknown collection metric: {group}")
        return self.collection_intervals.get(group, self.sample_interval)


def read_config_document(path):
    with path.open(encoding="utf-8") as source:
        settings = json.load(source)
    if not isinstance(settings, dict):
        raise ValueError("The agent configuration must be a JSON object.")
    known = {item.name for item in fields(AgentConfig) if item.init}
    unknown = set(settings) - known
    if unknown:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
    return settings


def config_from_document(settings, directory):
    settings = dict(settings)
    known = {item.name for item in fields(AgentConfig) if item.init}
    if set(settings) - known:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(set(settings) - known)))
    state_file = settings.get("state_file")
    if state_file is not None:
        if not isinstance(state_file, str) or not state_file:
            raise ValueError("state_file must be a nonempty path string or null.")
        path = Path(state_file).expanduser()
        settings["state_file"] = (path if path.is_absolute() else directory / path).resolve()
    if "encryption" in settings:
        settings["encryption"] = EncryptionSettings.from_document(settings["encryption"], directory).document()
    return AgentConfig(**settings)


def config_document(agent_config):
    result = asdict(agent_config)
    result.pop("manager_networks")
    if agent_config.state_file is not None:
        result["state_file"] = str(agent_config.state_file)
    return result


def write_json_atomic(path, document):
    """Replace a JSON file atomically, retaining existing POSIX permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as destination:
            temporary_path = Path(destination.name)
            json.dump(document, destination, indent=2, ensure_ascii=False, allow_nan=False)
            destination.write("\n")
            destination.flush()
            if previous is not None and hasattr(os, "fchmod"):
                os.fchmod(destination.fileno(), previous.st_mode & 0o777)
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    os.fchown(destination.fileno(), previous.st_uid, previous.st_gid)
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def apply_config_patch(document, patch, collection_only=False):
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Provide at least one configuration change.")
    if collection_only and set(patch) - {"sample_interval", "collection_intervals"}:
        raise ValueError("Remote updates support sample_interval and collection_intervals only.")
    updated = {**document, **patch}
    if "encryption" in patch:
        if not isinstance(patch["encryption"], dict):
            raise ValueError("encryption must be a JSON object.")
        updated["encryption"] = {**document.get("encryption", {}), **patch["encryption"]}
    if "collection_intervals" in patch:
        intervals = patch["collection_intervals"]
        if not isinstance(intervals, dict):
            raise ValueError("collection_intervals must be an object of metric names and seconds.")
        merged = dict(document.get("collection_intervals", {}))
        for group, seconds in intervals.items():
            if group not in COLLECTION_GROUPS:
                raise ValueError(f"Unknown collection metric: {group}")
            if seconds is None:
                merged.pop(group, None)
            else:
                merged[group] = seconds
        updated["collection_intervals"] = merged
    return updated


def update_config_file(path, patch, collection_only=False):
    document = read_config_document(path)
    updated = apply_config_patch(document, patch, collection_only)
    # Validate everything before touching the file or the live configuration.
    validated = config_from_document(updated, path.parent)
    write_json_atomic(path, updated)
    return validated


def load_config(args):
    settings = {}
    config_directory = Path.cwd()
    if args.config is None and DEFAULT_CONFIG_FILE.exists():
        args.config = DEFAULT_CONFIG_FILE
    if args.config:
        config_path = args.config.expanduser().resolve()
        args.config = config_path
        config_directory = config_path.parent
        settings = read_config_document(config_path)
    for name in ("listen_address", "port", "sample_interval"):
        value = getattr(args, name)
        if value is not None:
            settings[name] = value
    if args.manager:
        settings["allowed_managers"] = args.manager
    if args.state_file:
        settings["state_file"] = str(args.state_file.expanduser().resolve())
    return config_from_document(settings, config_directory)


class CollectionScheduler:
    """Independent monotonic schedules, expressed exclusively in seconds."""

    def __init__(self, agent_config):
        self.config = agent_config
        self.last_sample = {}
        self.sample_counts = {group: 0 for group in COLLECTION_GROUPS}

    def due(self, now):
        return {
            group for group in COLLECTION_GROUPS
            if group not in self.last_sample
            or now >= self.last_sample[group] + self.config.interval_for(group)
        }

    def collected(self, groups, now):
        for group in groups:
            self.last_sample[group] = now
            self.sample_counts[group] += 1

    def delay(self, now):
        return min(
            max(0.0, self.last_sample.get(group, now) + self.config.interval_for(group) - now)
            if group in self.last_sample else 0.0
            for group in COLLECTION_GROUPS
        )


def counter32(value):
    return rfc1902.Counter32(int(value) % (1 << 32))


def ticks(seconds):
    return rfc1902.TimeTicks(int(max(0, seconds) * 100) % (1 << 32))


def memory_kib(value):
    return rfc1902.Integer32(min(INTEGER_MAX, max(0, int(value) // 1024)))


def numeric_gauge(value):
    """Large/fractional gauges remain gauges, encoded as numeric OCTET STRINGs."""
    if not math.isfinite(float(value)):
        raise ValueError("Metric must be a finite number.")
    return rfc1902.OctetString(str(value))


def optional_psutil(name, *args, default=None, **kwargs):
    function = getattr(psutil, name, None)
    if function is None:
        return default
    try:
        return function(*args, **kwargs)
    except (psutil.Error, OSError, NotImplementedError):
        return default


def storage_row(index, description, total_bytes, used_bytes, storage_type):
    allocation = mmap.PAGESIZE
    while total_bytes // allocation > INTEGER_MAX:
        allocation *= 2
    total_units = int(total_bytes) // allocation
    used_units = min(total_units, (max(0, int(used_bytes)) + allocation - 1) // allocation)
    values = {
        1: rfc1902.Integer32(index), 2: rfc1902.ObjectIdentifier(storage_type),
        3: rfc1902.OctetString(description), 4: rfc1902.Integer32(allocation),
        5: rfc1902.Integer32(total_units), 6: rfc1902.Integer32(used_units),
    }
    return {STORAGE + (column, index): value for column, value in values.items()}


class HostMetrics:
    """Collect real host metrics and atomically publish an in-memory OID snapshot."""

    def __init__(self, agent_config, config_path=None, clock=None):
        self.config = agent_config
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.config_path = Path(config_path).resolve() if config_path is not None else None
        self.config_signature = self._config_signature()
        self.scheduler = CollectionScheduler(agent_config)
        self.group_oids = {group: set() for group in COLLECTION_GROUPS}
        self.system_values = {
            "sys_contact": agent_config.sys_contact,
            "sys_name": socket.gethostname() if agent_config.sys_name is None else agent_config.sys_name,
            "sys_location": agent_config.sys_location,
        }
        self._load_state()
        self.interface_indexes = {}
        self.interface_states = {}
        self.index_maps = {name: {} for name in ("addresses", "filesystems", "disks", "temperatures", "fans", "services")}
        self.cpu_samples = deque()
        self.cpu_sample_time = self.clock()
        self.latest_cpu_times = None
        psutil.cpu_percent(interval=None, percpu=True)
        psutil.cpu_times_percent(interval=None)
        self.snapshot = {}
        self.sorted_oids = []
        self.object_oids = set()
        self.refresh(force=True)

    def _config_signature(self):
        if self.config_path is None:
            return None
        try:
            details = self.config_path.stat()
        except FileNotFoundError:
            return None
        return details.st_mtime_ns, details.st_size, details.st_ino

    def collection_settings(self):
        return {
            "sample_interval": self.config.sample_interval,
            "collection_intervals": dict(self.config.collection_intervals),
        }

    def _activate_collection_settings(self, updated):
        self.config = replace(
            self.config, sample_interval=updated.sample_interval,
            collection_intervals=updated.collection_intervals,
        )
        self.scheduler.config = self.config
        self._publish_control_values(dict(self.snapshot))
        LOGGER.info("Collection settings applied (seconds): %s", self.collection_settings())

    def reload_configuration(self):
        """Watch local/SSH CLI updates even when sampling is set to 120 seconds."""
        if self.config_path is None:
            return False
        signature = self._config_signature()
        if signature == self.config_signature:
            return False
        self.config_signature = signature
        try:
            updated = config_from_document(read_config_document(self.config_path), self.config_path.parent)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Keeping last valid collection settings: %s", exc)
            return False
        if (
            updated.sample_interval != self.config.sample_interval
            or updated.collection_intervals != self.config.collection_intervals
        ):
            self._activate_collection_settings(updated)
            return True
        return False

    def set_collection_configuration(self, patch):
        if self.config_path is None:
            raise ValueError("Start the agent with a configuration file to persist remote changes.")
        updated = update_config_file(self.config_path, patch, collection_only=True)
        self.config_signature = self._config_signature()
        self._activate_collection_settings(updated)

    def _load_state(self):
        path = self.config.state_file
        if path is None or not path.exists():
            return
        with path.open(encoding="utf-8") as source:
            saved = json.load(source)
        if not isinstance(saved, dict) or set(saved) - set(self.system_values):
            raise ValueError(f"Invalid system-value state file: {path}")
        for name, value in saved.items():
            validate_display_string(name, value)
        self.system_values.update(saved)

    def set_system_values(self, changes):
        """Persist all SET values before publishing any of them."""
        updated = {**self.system_values, **changes}
        path = self.config.state_file
        if path is not None:
            write_json_atomic(path, updated)
        snapshot = dict(self.snapshot)
        for oid, name in SYSTEM_WRITABLE.items():
            snapshot[oid] = rfc1902.OctetString(updated[name].encode("utf-8"))
        self.system_values = updated
        self.snapshot = snapshot

    def _cpu_average(self, now):
        loads = tuple(psutil.cpu_percent(interval=None, percpu=True))
        self.latest_cpu_times = psutil.cpu_times_percent(interval=None)
        idle = self.latest_cpu_times.idle
        if self.cpu_samples and len(self.cpu_samples[-1][2]) != len(loads):
            self.cpu_samples.clear()
        self.cpu_samples.append((self.cpu_sample_time, now, loads, idle))
        self.cpu_sample_time = now
        cutoff = now - 60.0
        while self.cpu_samples and self.cpu_samples[0][1] <= cutoff:
            self.cpu_samples.popleft()
        sums = [0.0] * len(loads)
        idle_sum = 0.0
        duration = 0.0
        for start, end, sample, sample_idle in self.cpu_samples:
            weight = max(0.0, end - max(start, cutoff))
            duration += weight
            idle_sum += sample_idle * weight
            for index, value in enumerate(sample):
                sums[index] += value * weight
        if duration <= 0:
            return loads, idle
        return [value / duration for value in sums], idle_sum / duration

    def _assign_interface_indexes(self, names):
        # Keep indexes stable while running; use native indexes where available.
        try:
            native = {name: index for index, name in socket.if_nameindex()}
        except (OSError, AttributeError):
            native = {}
        reserved = set(native.values()) | set(self.interface_indexes.values())
        for name in sorted(names):
            if name in self.interface_indexes:
                continue
            index = native.get(name)
            if index is None or index in self.interface_indexes.values():
                index = max(reserved, default=0) + 1
            self.interface_indexes[name] = index
            reserved.add(index)

    def _collect_system(self, now):
        description = (
            f"Python SNMP host agent; {platform.platform()}; "
            f"{platform.machine()}; Python {platform.python_version()}"
        ).encode("utf-8")[:255]
        snapshot = {
            SYSTEM + (1, 0): rfc1902.OctetString(description),
            SYSTEM + (2, 0): rfc1902.ObjectIdentifier(EXAMPLE_ROOT + (1,)),
            SYSTEM + (3, 0): ticks(now - self.started),
            SYSTEM + (7, 0): rfc1902.Integer32(72),
            (1, 3, 6, 1, 2, 1, 25, 1, 1, 0): ticks(time.time() - psutil.boot_time()),
        }
        for oid, name in SYSTEM_WRITABLE.items():
            snapshot[oid] = rfc1902.OctetString(self.system_values[name].encode("utf-8"))
        snapshot[mib.HR_SYSTEM + (5, 0)] = rfc1902.Gauge32(len(optional_psutil("users", default=[])))
        snapshot[mib.HR_SYSTEM + (6, 0)] = rfc1902.Gauge32(len(optional_psutil("pids", default=[])))
        for column, value in enumerate((platform.system(), platform.release(), platform.version(),
                                         platform.machine(), platform.processor(), platform.python_version()), 1):
            snapshot[mib.HOST + (column, 0)] = rfc1902.OctetString(value)
        snapshot[mib.HOST + (7, 0)] = numeric_gauge(psutil.boot_time())
        return snapshot

    def _index(self, table_name, key):
        indexes = self.index_maps[table_name]
        if key not in indexes:
            indexes[key] = max(indexes.values(), default=0) + 1
        return indexes[key]

    def _interface_type(self, name, stats, addresses):
        if "loopback" in (getattr(stats, "flags", "") or "") or any(
            record.family in (socket.AF_INET, socket.AF_INET6)
            and ipaddress.ip_address(record.address.split("%", 1)[0]).is_loopback for record in addresses
        ):
            return 24
        if sys.platform.startswith("linux"):
            device = Path("/sys/class/net") / name
            try:
                if (device / "wireless").is_dir():
                    return 71
                if (device / "type").read_text().strip() == "1":
                    return 6
            except OSError:
                pass
        return 1  # Other: do not invent a link-layer type that the backend lacks.

    def _collect_addresses(self, snapshot, name, index, records):
        for record in records:
            if record.family == getattr(psutil, "AF_LINK", None):
                try:
                    raw = bytes.fromhex(record.address.replace(":", "").replace("-", ""))
                    snapshot[IF_TABLE + (6, index)] = rfc1902.OctetString(raw)
                except ValueError:
                    pass
                continue
            if record.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            address_index = self._index("addresses", (name, record.family, record.address))
            fields = {1: rfc1902.Integer32(address_index), 2: rfc1902.OctetString(name),
                      3: rfc1902.Integer32(index), 4: rfc1902.OctetString(record.address),
                      6: rfc1902.Integer32(4 if record.family == socket.AF_INET else 6)}
            for column, attribute in ((5, "netmask"), (7, "broadcast"), (8, "ptp")):
                value = getattr(record, attribute, None)
                if value is not None:
                    fields[column] = rfc1902.OctetString(value)
            snapshot.update({mib.ADDRESSES + (column, address_index): value for column, value in fields.items()})
            if record.family == socket.AF_INET:
                suffix = tuple(int(part) for part in record.address.split("."))
                snapshot[mib.IP_ADDRESS_TABLE + (1,) + suffix] = rfc1902.IpAddress(record.address)
                snapshot[mib.IP_ADDRESS_TABLE + (2,) + suffix] = rfc1902.Integer32(index)
                if record.netmask:
                    snapshot[mib.IP_ADDRESS_TABLE + (3,) + suffix] = rfc1902.IpAddress(record.netmask)

    def _collect_interfaces(self, groups, now):
        collected = {group: {} for group in groups}
        counters = psutil.net_io_counters(pernic=True, nowrap=True) or {}
        statistics = psutil.net_if_stats() if "interfaces" in groups else {}
        addresses = psutil.net_if_addrs() if "interfaces" in groups else {}
        names = set(statistics) | set(addresses) | set(counters)
        self._assign_interface_indexes(names)
        if "interfaces" in groups:
            collected["interfaces"][(1, 3, 6, 1, 2, 1, 2, 1, 0)] = rfc1902.Integer32(len(names))
        for name in sorted(names):
            index = self.interface_indexes[name]
            if "interfaces" in groups:
                snapshot = collected["interfaces"]
                stats = statistics.get(name)
                operational = 4 if stats is None else (1 if stats.isup else 2)
                flags = set((getattr(stats, "flags", "") or "").split(",")) if stats else set()
                administrative = (1 if "up" in flags else 2) if flags - {""} else (
                    1 if stats and stats.isup else 2
                )
                previous_state, changed_at = self.interface_states.get(name, (operational, 0))
                if previous_state != operational:
                    changed_at = now - self.started
                self.interface_states[name] = operational, changed_at
                speed_mbps = max(0, int(stats.speed)) if stats else 0
                for column, value in {
                    1: rfc1902.Integer32(index),
                    2: rfc1902.OctetString(name.encode("utf-8")),
                    3: rfc1902.Integer32(self._interface_type(name, stats, addresses.get(name, []))),
                    4: rfc1902.Integer32(max(0, stats.mtu) if stats else 0),
                    5: rfc1902.Gauge32(min((1 << 32) - 1, speed_mbps * 1_000_000)),
                    7: rfc1902.Integer32(administrative),
                    8: rfc1902.Integer32(operational),
                    9: ticks(changed_at),
                }.items():
                    snapshot[IF_TABLE + (column, index)] = value
                snapshot[IFX_TABLE + (1, index)] = rfc1902.OctetString(name.encode("utf-8"))
                snapshot[IFX_TABLE + (15, index)] = rfc1902.Gauge32(min((1 << 32) - 1, speed_mbps))
                self._collect_addresses(snapshot, name, index, addresses.get(name, []))
            io = counters.get(name)
            if io is None:
                continue
            if "ifInOctets" in groups:
                collected["ifInOctets"][IF_TABLE + (10, index)] = counter32(io.bytes_recv)
                collected["ifInOctets"][IFX_TABLE + (6, index)] = rfc1902.Counter64(io.bytes_recv % (1 << 64))
            if "ifOutOctets" in groups:
                collected["ifOutOctets"][IF_TABLE + (16, index)] = counter32(io.bytes_sent)
                collected["ifOutOctets"][IFX_TABLE + (10, index)] = rfc1902.Counter64(io.bytes_sent % (1 << 64))
            if "interfaces" in groups:
                snapshot = collected["interfaces"]
                for column, value in {13: io.dropin, 14: io.errin, 19: io.dropout, 20: io.errout}.items():
                    snapshot[IF_TABLE + (column, index)] = counter32(value)
                # Total packets must not be mislabeled as unicast-only packets.
                snapshot[PACKET_TABLE + (1, index)] = counter32(io.packets_recv)
                snapshot[PACKET_TABLE + (2, index)] = counter32(io.packets_sent)
        return collected

    def _collect_cpu(self, now):
        cpu_loads, cpu_idle = self._cpu_average(now)
        snapshot = {UCD + (11, 11, 0): rfc1902.Integer32(round(max(0, min(100, cpu_idle))))}
        for index, load in enumerate(cpu_loads, start=1):
            snapshot[CPU_LOAD + (index,)] = rfc1902.Integer32(round(max(0, min(100, load))))
        snapshot[mib.CPU + (1, 0)] = rfc1902.Gauge32(len(cpu_loads))
        physical = optional_psutil("cpu_count", logical=False)
        if physical is not None:
            snapshot[mib.CPU + (2, 0)] = rfc1902.Gauge32(physical)
        frequency = optional_psutil("cpu_freq")
        if frequency is not None:
            for column, attribute in ((3, "current"), (4, "min"), (5, "max")):
                value = getattr(frequency, attribute, None)
                if value is not None:
                    snapshot[mib.CPU + (column, 0)] = numeric_gauge(value)
        for column, name in ((6, "user"), (7, "system"), (8, "idle"), (9, "nice"),
                             (10, "iowait"), (11, "irq"), (12, "softirq"), (13, "steal")):
            value = getattr(self.latest_cpu_times, name, None)
            if value is not None:
                snapshot[mib.CPU + (column, 0)] = numeric_gauge(value)
        stats = optional_psutil("cpu_stats")
        for column, name in ((14, "ctx_switches"), (15, "interrupts"), (16, "soft_interrupts"), (17, "syscalls")):
            value = getattr(stats, name, None)
            if value is not None:
                snapshot[mib.CPU + (column, 0)] = rfc1902.Counter64(int(value) % (1 << 64))
        return snapshot

    def _collect_memory(self):
        memory = psutil.virtual_memory()
        snapshot = {
            (1, 3, 6, 1, 2, 1, 25, 2, 2, 0): memory_kib(memory.total),
            UCD + (4, 5, 0): memory_kib(memory.total),
            UCD + (4, 6, 0): memory_kib(memory.available),
        }
        used_bytes = max(0, memory.total - min(memory.total, memory.available))
        snapshot.update(storage_row(1, "Physical Memory", memory.total, used_bytes, RAM_TYPE))
        for column, value in ((1, memory.total), (2, memory.available), (3, used_bytes)):
            snapshot[mib.MEMORY + (column, 0)] = numeric_gauge(value)
        for column, name in ((4, "free"), (5, "buffers"), (6, "cached"), (7, "active"), (8, "inactive")):
            value = getattr(memory, name, None)
            if value is not None:
                snapshot[mib.MEMORY + (column, 0)] = numeric_gauge(value)
        for column, name in ((14, "buffers"), (15, "cached")):
            value = getattr(memory, name, None)
            if value is not None:
                snapshot[UCD + (4, column, 0)] = memory_kib(value)
        snapshot[mib.MEMORY + (9, 0)] = numeric_gauge(100 * used_bytes / memory.total if memory.total else 0)
        swap = optional_psutil("swap_memory")
        if swap is not None:
            snapshot[UCD + (4, 3, 0)], snapshot[UCD + (4, 4, 0)] = memory_kib(swap.total), memory_kib(swap.free)
            snapshot.update(storage_row(2, "Swap Memory", swap.total, swap.used, (1, 3, 6, 1, 2, 1, 25, 2, 1, 3)))
            for column, value in ((10, swap.total), (11, swap.used), (12, swap.free), (13, swap.percent)):
                snapshot[mib.MEMORY + (column, 0)] = numeric_gauge(value)
        return snapshot

    def _collect_storage(self):
        snapshot = {}
        for partition in optional_psutil("disk_partitions", all=False, default=[]) or []:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, psutil.Error):
                continue
            index = self._index("filesystems", partition.mountpoint)
            storage_index = index + 2  # 1=physical RAM, 2=swap; never disturb RAM autodiscovery.
            snapshot.update(storage_row(storage_index, partition.mountpoint, usage.total, usage.used,
                                        (1, 3, 6, 1, 2, 1, 25, 2, 1, 4)))
            fields = {1: rfc1902.Integer32(index), 2: rfc1902.OctetString(partition.device),
                      3: rfc1902.OctetString(partition.mountpoint), 4: rfc1902.OctetString(partition.fstype),
                      5: rfc1902.OctetString(partition.opts), 6: numeric_gauge(usage.total),
                      7: numeric_gauge(usage.used), 8: numeric_gauge(usage.free), 9: numeric_gauge(usage.percent),
                      10: rfc1902.Integer32(storage_index)}
            snapshot.update({mib.FILESYSTEM + (column, index): value for column, value in fields.items()})
        return snapshot

    def _collect_diskio(self):
        snapshot = {}
        for name, counters in sorted((optional_psutil("disk_io_counters", perdisk=True, nowrap=True, default={}) or {}).items()):
            index = self._index("disks", name)
            snapshot[mib.DISKIO + (1, index)], snapshot[mib.DISKIO + (2, index)] = rfc1902.Integer32(index), rfc1902.OctetString(name)
            snapshot[mib.UCD_DISKIO + (1, index)], snapshot[mib.UCD_DISKIO + (2, index)] = rfc1902.Integer32(index), rfc1902.OctetString(name)
            for column, name in ((3, "read_bytes"), (4, "write_bytes"), (5, "read_count"), (6, "write_count"),
                                 (7, "read_time"), (8, "write_time"), (9, "busy_time"), (10, "read_merged_count"), (11, "write_merged_count")):
                value = getattr(counters, name, None)
                if value is not None:
                    snapshot[mib.DISKIO + (column, index)] = rfc1902.Counter64(int(value) % (1 << 64))
                    if column in (3, 4, 5, 6):
                        snapshot[mib.UCD_DISKIO + (column, index)] = counter32(value)
                    if column in (3, 4):
                        snapshot[mib.UCD_DISKIO + (column + 6, index)] = rfc1902.Counter64(int(value) % (1 << 64))
        return snapshot

    def _collect_processes(self):
        snapshot = {}
        attributes = ("pid", "name", "exe", "username", "status", "memory_info", "num_threads", "ppid", "create_time", "cpu_times")
        status_codes = {"running": 1, "sleeping": 2, "disk-sleep": 2, "idle": 2, "stopped": 3,
                        "tracing-stop": 3, "zombie": 4, "dead": 4}
        try:
            processes = psutil.process_iter(attrs=attributes, ad_value=None)
            for process in processes:
                try:
                    info = process.info
                    pid = info["pid"]
                    if pid <= 0 or pid > INTEGER_MAX:
                        continue
                    snapshot[mib.PROCESS_TABLE + (1, pid)] = rfc1902.Integer32(pid)
                    snapshot[mib.PROCESSES + (1, pid)] = rfc1902.Integer32(pid)
                    for column, key in ((3, "name"), (4, "username"), (5, "status"), (11, "exe")):
                        if info.get(key) is not None:
                            snapshot[mib.PROCESSES + (column, pid)] = rfc1902.OctetString(info[key])
                    if info.get("name") is not None:
                        snapshot[mib.PROCESS_TABLE + (2, pid)] = rfc1902.OctetString(info["name"].encode("utf-8")[:64])
                    if info.get("exe") is not None:
                        snapshot[mib.PROCESS_TABLE + (4, pid)] = rfc1902.OctetString(info["exe"].encode("utf-8")[:128])
                        snapshot[mib.PROCESS_TABLE + (6, pid)] = rfc1902.Integer32(4)  # application
                    if info.get("status") in status_codes:
                        snapshot[mib.PROCESS_TABLE + (7, pid)] = rfc1902.Integer32(status_codes[info["status"]])
                    for column, key, syntax in ((2, "ppid", rfc1902.Integer32), (6, "num_threads", rfc1902.Gauge32),
                                                (10, "create_time", numeric_gauge)):
                        if info.get(key) is not None:
                            snapshot[mib.PROCESSES + (column, pid)] = syntax(info[key])
                    try:
                        snapshot[mib.PROCESSES + (7, pid)] = numeric_gauge(process.cpu_percent(interval=None))
                    except (psutil.Error, OSError):
                        pass
                    memory = info.get("memory_info")
                    if memory is not None:
                        snapshot[mib.PROCESS_PERF + (2, pid)] = memory_kib(memory.rss)
                        snapshot[mib.PROCESSES + (8, pid)], snapshot[mib.PROCESSES + (9, pid)] = numeric_gauge(memory.rss), numeric_gauge(memory.vms)
                    times = info.get("cpu_times")
                    if times is not None:
                        snapshot[mib.PROCESS_PERF + (1, pid)] = rfc1902.Integer32(min(INTEGER_MAX, int((times.user + times.system) * 100)))
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError, NotImplementedError):
            pass
        return snapshot

    def _collect_connections(self):
        connections = optional_psutil("net_connections", kind="inet")
        if connections is None:
            return {}
        tcp = [item for item in connections if item.type == socket.SOCK_STREAM]
        statuses = [item.status for item in tcp]
        values = (len(tcp), statuses.count("ESTABLISHED"), statuses.count("LISTEN"),
                  statuses.count("TIME_WAIT"), statuses.count("CLOSE_WAIT"), statuses.count("SYN_SENT"),
                  sum(item.type == socket.SOCK_DGRAM for item in connections))
        snapshot = {mib.CONNECTIONS + (column, 0): rfc1902.Gauge32(value) for column, value in enumerate(values, 1)}
        snapshot[(1, 3, 6, 1, 2, 1, 6, 9, 0)] = rfc1902.Gauge32(values[1] + values[4])
        return snapshot

    def _collect_sensors(self):
        snapshot = {}
        battery = optional_psutil("sensors_battery")
        if battery is not None:
            snapshot[mib.SENSORS + (1, 0)], snapshot[mib.SENSORS + (2, 0)] = numeric_gauge(battery.percent), numeric_gauge(battery.secsleft)
            snapshot[mib.SENSORS + (3, 0)] = rfc1902.Gauge32(int(battery.power_plugged))
        for hardware, sensors in sorted((optional_psutil("sensors_temperatures", default={}) or {}).items()):
            for ordinal, sensor in enumerate(sensors):
                index = self._index("temperatures", (hardware, sensor.label, ordinal))
                fields = {1: rfc1902.Integer32(index), 2: rfc1902.OctetString(hardware), 3: rfc1902.OctetString(sensor.label)}
                for column, attribute in ((4, "current"), (5, "high"), (6, "critical")):
                    value = getattr(sensor, attribute, None)
                    if value is not None:
                        fields[column] = numeric_gauge(value)
                snapshot.update({mib.SENSORS + (4, column, index): value for column, value in fields.items()})
        for hardware, fans in sorted((optional_psutil("sensors_fans", default={}) or {}).items()):
            for ordinal, fan in enumerate(fans):
                index = self._index("fans", (hardware, fan.label, ordinal))
                fields = {1: rfc1902.Integer32(index), 2: rfc1902.OctetString(hardware),
                          3: rfc1902.OctetString(fan.label), 4: rfc1902.Gauge32(fan.current)}
                snapshot.update({mib.SENSORS + (5, column, index): value for column, value in fields.items()})
        return snapshot

    def _collect_services(self):
        snapshot = {}
        services = optional_psutil("win_service_iter", default=[]) or []
        try:
            for service in services:
                try:
                    info = service.as_dict()
                    index = self._index("services", info["name"])
                    snapshot[mib.SERVICES + (1, index)] = rfc1902.Integer32(index)
                    for column, key in ((2, "name"), (3, "display_name"), (4, "status"), (5, "start_type"),
                                        (7, "username"), (8, "description"), (9, "binpath")):
                        if info.get(key) is not None:
                            snapshot[mib.SERVICES + (column, index)] = rfc1902.OctetString(info[key])
                    if info.get("pid") is not None:
                        snapshot[mib.SERVICES + (6, index)] = rfc1902.Integer32(info["pid"])
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError, NotImplementedError):
            pass
        return snapshot

    def _collect_load(self):
        snapshot = {}
        try:
            load_averages = psutil.getloadavg()
        except (AttributeError, NotImplementedError, OSError):
            load_averages = ()
        for index, load in enumerate(load_averages, start=1):
            snapshot[UCD + (10, 1, 1, index)] = rfc1902.Integer32(index)
            snapshot[UCD + (10, 1, 2, index)] = rfc1902.OctetString(("Load-1", "Load-5", "Load-15")[index - 1])
            snapshot[UCD + (10, 1, 3, index)] = rfc1902.OctetString(f"{load:.2f}")
        return snapshot

    def _publish_control_values(self, snapshot):
        snapshot[COLLECTION_CONFIG_OID] = rfc1902.OctetString(json.dumps(self.collection_settings()))
        for group in COLLECTION_GROUPS:
            snapshot[collection_metadata_oid(group, 1)] = rfc1902.OctetString(group)
            snapshot[collection_metadata_oid(group, 2)] = rfc1902.OctetString(str(self.config.interval_for(group)))
            elapsed = self.scheduler.last_sample.get(group, self.started) - self.started
            snapshot[collection_metadata_oid(group, 3)] = rfc1902.OctetString(f"{elapsed:.9f}")
            snapshot[collection_metadata_oid(group, 4)] = counter32(self.scheduler.sample_counts[group])
        self.snapshot = snapshot
        self.sorted_oids = sorted(snapshot)
        self.object_oids = {oid[:-1] for oid in snapshot}

    def refresh(self, force=False):
        now = self.clock()
        due = set(COLLECTION_GROUPS) if force else self.scheduler.due(now)
        if not due:
            return set()
        collected = {}
        if "system" in due:
            collected["system"] = self._collect_system(now)
        network = due & {"interfaces", "ifInOctets", "ifOutOctets"}
        if network:
            collected.update(self._collect_interfaces(network, now))
        if "cpu" in due:
            collected["cpu"] = self._collect_cpu(now)
        if "memory" in due:
            collected["memory"] = self._collect_memory()
        if "load" in due:
            collected["load"] = self._collect_load()
        for group, collector in (("storage", self._collect_storage), ("diskio", self._collect_diskio),
                                 ("processes", self._collect_processes), ("connections", self._collect_connections),
                                 ("sensors", self._collect_sensors), ("services", self._collect_services)):
            if group in due:
                collected[group] = collector()
        snapshot = dict(self.snapshot)
        for group, values in collected.items():
            for old_oid in self.group_oids[group]:
                snapshot.pop(old_oid, None)
            snapshot.update(values)
            self.group_oids[group] = set(values)
        self.scheduler.collected(due, self.clock())
        self._publish_control_values(snapshot)
        return due


class HostMibController(instrum.AbstractMibInstrumController):
    """Serve the snapshot while honoring PySNMP's per-community access checks."""

    def __init__(self, metrics):
        self.metrics = metrics

    @staticmethod
    def _denied(operation, binding, index, request_context):
        access = request_context.get("acFun")
        return bool(access and access(operation, binding, **dict(request_context, idx=index)))

    def read_variables(self, *var_binds, **request_context):
        snapshot = self.metrics.snapshot
        results = []
        for index, (name, _) in enumerate(var_binds):
            oid = tuple(name)
            value = snapshot.get(oid)
            if value is None:
                known = oid in self.metrics.object_oids or oid[:-1] in self.metrics.object_oids
                value = rfc1905.noSuchInstance if known else rfc1905.noSuchObject
            elif self._denied("read", (name, value), index, request_context):
                value = rfc1905.noSuchObject
            results.append((rfc1902.ObjectName(oid), value))
        return results

    def read_next_variables(self, *var_binds, **request_context):
        snapshot = self.metrics.snapshot
        oids = self.metrics.sorted_oids
        results = []
        for index, (name, _) in enumerate(var_binds):
            result = (name, rfc1905.endOfMibView)
            position = bisect.bisect_right(oids, tuple(name))
            for next_position in range(position, len(oids)):
                oid = oids[next_position]
                binding = (rfc1902.ObjectName(oid), snapshot[oid])
                try:
                    if self._denied("read", binding, index, request_context):
                        continue
                except error.NoAccessError:
                    # SNMPv1 walks must skip Counter64 objects.
                    continue
                result = binding
                break
            results.append(result)
        return results

    def write_variables(self, *var_binds, **request_context):
        configuration_binds = [binding for binding in var_binds if tuple(binding[0]) == COLLECTION_CONFIG_OID]
        if configuration_binds:
            # Two separate files cannot be committed as one SNMP transaction.
            # Configuration updates therefore use a single dedicated binding.
            if len(var_binds) != 1:
                raise error.InconsistentValueError(idx=0)
            name, value = configuration_binds[0]
            if self.metrics.config.write_community is None:
                raise error.NotWritableError(name=name, idx=0)
            if self._denied("write", (name, value), 0, request_context):
                raise error.NoAccessError(name=name, idx=0)
            if self.metrics.config_path is None:
                raise error.ResourceUnavailableError(name=name, idx=0)
            if value.tagSet != rfc1902.OctetString.tagSet:
                raise error.WrongTypeError(name=name, idx=0)
            if len(value.asOctets()) > 8192:
                raise error.WrongLengthError(name=name, idx=0)
            try:
                patch = json.loads(value.asOctets().decode("utf-8"))
                self.metrics.set_collection_configuration(patch)
            except (ValueError, UnicodeDecodeError) as exc:
                raise error.WrongValueError(name=name, idx=0) from exc
            except OSError as exc:
                LOGGER.error("Unable to persist collection settings: %s", exc)
                raise error.CommitFailedError(name=name, idx=0) from exc
            return [(name, self.metrics.snapshot[COLLECTION_CONFIG_OID])]
        changes = {}
        for index, (name, value) in enumerate(var_binds):
            oid = tuple(name)
            if self.metrics.config.write_community is None or oid not in SYSTEM_WRITABLE:
                raise error.NotWritableError(name=name, idx=index)
            if self._denied("write", (name, value), index, request_context):
                raise error.NoAccessError(name=name, idx=index)
            if value.tagSet != rfc1902.OctetString.tagSet:
                raise error.WrongTypeError(name=name, idx=index)
            raw = value.asOctets()
            if len(raw) > 255:
                raise error.WrongLengthError(name=name, idx=index)
            try:
                changes[SYSTEM_WRITABLE[oid]] = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise error.WrongEncodingError(name=name, idx=index) from exc
        try:
            self.metrics.set_system_values(changes)
        except OSError as exc:
            LOGGER.error("Unable to persist SET values: %s", exc)
            raise error.CommitFailedError(idx=0) from exc
        LOGGER.info("Updated system values: %s", ", ".join(sorted(changes)))
        return [(name, self.metrics.snapshot[tuple(name)]) for name, _ in var_binds]


class ManagerUdpTransport(udp.UdpTransport):
    """Apply manager ACLs and optionally decrypt/encrypt full SNMP datagrams."""

    def __init__(self, agent_config):
        super().__init__()
        self.agent_config = agent_config
        self.cipher = RsaEnvelope.from_settings(EncryptionSettings.from_document(agent_config.encryption))
        self.correlations = OrderedDict()

    def datagram_received(self, datagram, transport_address):
        if not self.agent_config.allows_manager(transport_address[0]):
            LOGGER.debug("Ignoring SNMP datagram from %s", transport_address[0])
            return
        if self.cipher is not None:
            try:
                packet = self.cipher.decrypt(datagram, REQUEST)
                request_id = snmp_request_id(packet.payload)
                now = time.monotonic()
                while self.correlations and next(iter(self.correlations.values()))[1] < now - MAX_AGE_SECONDS:
                    self.correlations.popitem(last=False)
                key = tuple(transport_address), request_id
                if len(self.correlations) >= 4096 and key not in self.correlations:
                    raise CryptoProtocolError("Too many pending encrypted requests.")
                self.correlations.pop(key, None)
                self.correlations[key] = packet.message_id, now
                datagram = packet.payload
            except CryptoProtocolError as exc:
                LOGGER.debug("Rejected encrypted request from %s: %s", transport_address[0], exc)
                return
        super().datagram_received(datagram, transport_address)

    def send_message(self, outgoing_message, transport_address):
        if self.cipher is not None:
            try:
                key = tuple(transport_address), snmp_request_id(outgoing_message)
                correlation = self.correlations.pop(key, None)
                if correlation is None:
                    raise CryptoProtocolError("No matching encrypted request for the response.")
                outgoing_message, _ = self.cipher.encrypt(outgoing_message, RESPONSE, correlation[0])
            except CryptoProtocolError as exc:
                LOGGER.debug("Encrypted response rejected: %s", exc)
                return
        super().send_message(outgoing_message, transport_address)


def configure_communities(snmp_engine, agent_config):
    config.add_context(snmp_engine, b"")
    communities = [("host-read", agent_config.read_community, False)]
    if agent_config.write_community is not None:
        communities.append(("host-write", agent_config.write_community, True))
    for security_name, community, writable in communities:
        config.add_v1_system(snmp_engine, security_name, community, securityName=security_name)
        for security_model in (1, 2):
            group = f"{security_name}-{security_model}"
            read_view, write_view = group + "-read", group + "-write"
            config.add_vacm_group(snmp_engine, group, security_model, security_name)
            config.add_vacm_access(
                snmp_engine, group, b"", security_model, "noAuthNoPriv", "exact",
                read_view, write_view, "",
            )
            config.add_vacm_view(snmp_engine, read_view, "included", (1, 3, 6, 1), b"")
            # An explicit excluded view is important: PySNMP 7.1.30's VACM
            # returns (rather than raises) a denial when a view has no entries.
            config.add_vacm_view(
                snmp_engine, write_view, "included" if writable else "excluded",
                SYSTEM if writable else (1, 3, 6, 1), b"",
            )
            if writable:
                config.add_vacm_view(snmp_engine, write_view, "included", COLLECTION_CONFIG_OID, b"")


async def serve(agent_config, metrics, external_stop=None, install_signal_handlers=True):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = []
    for signum in ((signal.SIGINT, signal.SIGTERM) if install_signal_handlers else ()):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed_signals.append(signum)
        except (NotImplementedError, RuntimeError):
            # Windows uses asyncio.run()'s Ctrl+C handling instead.
            pass

    snmp_engine = engine.SnmpEngine()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Bind synchronously so permission/port-in-use errors reach the CLI.
        listener.bind((agent_config.listen_address, agent_config.port))
        listener.setblocking(False)
        transport = ManagerUdpTransport(agent_config).open_server_mode(sock=listener)
        config.add_transport(snmp_engine, udp.DOMAIN_NAME, transport)
        configure_communities(snmp_engine, agent_config)
        snmp_context = context.SnmpContext(snmp_engine)
        snmp_context.unregister_context_name(b"")
        snmp_context.register_context_name(b"", HostMibController(metrics))
        responders = [
            responder(snmp_engine, snmp_context)
            for responder in (
                cmdrsp.GetCommandResponder, cmdrsp.NextCommandResponder,
                cmdrsp.BulkCommandResponder, cmdrsp.SetCommandResponder,
            )
        ]
        LOGGER.info("Listening for SNMPv1/v2c on %s:%s/udp", agent_config.listen_address, agent_config.port)
        LOGGER.info("Traffic encryption: %s", "RSA-OAEP/AES-GCM + RSA-PSS" if transport.cipher else "disabled")
        LOGGER.info("Allowed managers: %s", ", ".join(agent_config.allowed_managers))
        LOGGER.info("Sample interval: %ss; system SET: %s", agent_config.sample_interval,
                    "enabled" if agent_config.write_community else "disabled")
        LOGGER.info("Interfaces: %s", ", ".join(
            f"{name}={index}" for name, index in sorted(metrics.interface_indexes.items())
        ))
        while not stop.is_set() and not (external_stop is not None and external_stop.is_set()):
            refresh_failed = False
            try:
                metrics.reload_configuration()
                metrics.refresh()
            except (psutil.Error, OSError) as exc:
                refresh_failed = True
                LOGGER.error("Metric refresh failed; retaining previous snapshot: %s", exc)
            delay = CONFIG_CHECK_INTERVAL if refresh_failed else min(
                CONFIG_CHECK_INTERVAL, max(0.001, metrics.scheduler.delay(metrics.clock()))
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        for responder in responders:
            responder.close(snmp_engine)
    finally:
        if snmp_engine.transport_dispatcher is not None:
            snmp_engine.transport_dispatcher.close_dispatcher()
        listener.close()
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        LOGGER.info("SNMP agent stopped.")


def cli_config_patch(args):
    patch = {}
    for name in ("listen_address", "port", "sample_interval"):
        value = getattr(args, name, None)
        if value is not None:
            patch[name] = value
    if args.manager:
        patch["allowed_managers"] = args.manager
    if args.state_file:
        patch["state_file"] = str(args.state_file)
    for name, text in args.assignment:
        if name in patch:
            raise ValueError(f"Repeated configuration change: {name}")
        try:
            patch[name] = json.loads(text)
        except ValueError:
            patch[name] = text
    intervals = {}
    for group, text in args.metric_interval:
        if group in intervals:
            raise ValueError(f"Repeated collection metric: {group}")
        intervals[group] = positive_seconds(text)
    for group in args.clear_interval:
        if group in intervals:
            raise ValueError(f"Repeated collection metric: {group}")
        intervals[group] = None
    if intervals:
        if "collection_intervals" in patch:
            raise ValueError("Use either --set collection_intervals or --interval/--clear-interval.")
        patch["collection_intervals"] = intervals
    encryption = {}
    if getattr(args, "encryption_mode", None) is not None:
        encryption["enabled"] = args.encryption_mode == "on"
    for name, option in (("private_key_file", "rsa_private_key"), ("peer_public_key_file", "rsa_peer_public_key")):
        value = getattr(args, option, None)
        if value is not None:
            encryption[name] = str(value)
    if encryption:
        if "encryption" in patch:
            raise ValueError("Use either --set encryption or the --encryption/--rsa-* options.")
        patch["encryption"] = encryption
    # Validate a remote patch locally as well, without touching a local file.
    if args.remote:
        candidate = apply_config_patch({}, patch, collection_only=True)
        config_from_document(candidate, Path.cwd())
    return patch


def parse_remote_target(target):
    address = target.removeprefix("udp:")
    parts = address.split(":")
    if len(parts) > 2 or not parts[0]:
        raise ValueError("Remote target must be an IPv4/hostname, optionally with :port or udp: prefix.")
    port = int(parts[1]) if len(parts) == 2 else 161
    if not 1 <= port <= 65535:
        raise ValueError("Remote UDP port must be between 1 and 65535.")
    return parts[0], port


async def remote_configuration(target, community, version="2c", patch=None, timeout=2.0, encryption_config=None):
    """Read/update the remote agent using the same CLI, over standard SNMP."""
    cipher = RsaEnvelope.from_settings(load_encryption_settings(encryption_config)) if encryption_config else None
    proxy_context = EncryptedSnmpProxy(target, cipher) if cipher else contextlib.nullcontext()
    with proxy_context as proxy:
        return await _remote_configuration_request(
            proxy.snmp_target if proxy else target, community, version, patch, timeout,
        )


async def _remote_configuration_request(target, community, version, patch, timeout):
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData, ContextData, ObjectIdentity, ObjectType,
        UdpTransportTarget, get_cmd, set_cmd,
    )

    client_engine = engine.SnmpEngine()
    try:
        transport = await UdpTransportTarget.create(
            parse_remote_target(target), timeout=positive_seconds(timeout), retries=0,
        )
        binding = ObjectType(ObjectIdentity(COLLECTION_CONFIG_OID))
        operation = get_cmd
        if patch is not None:
            binding = ObjectType(
                ObjectIdentity(COLLECTION_CONFIG_OID),
                rfc1902.OctetString(json.dumps(patch, allow_nan=False)),
            )
            operation = set_cmd
        indication, status, _, bindings = await operation(
            client_engine, CommunityData(community, mpModel=0 if version == "1" else 1),
            transport, ContextData(), binding, lookupMib=False,
        )
        if indication:
            raise RuntimeError(f"Remote SNMP request failed: {indication}")
        if status:
            raise RuntimeError(f"Remote SNMP request rejected: {status.prettyPrint()}")
        try:
            return json.loads(bindings[0][1].asOctets().decode("utf-8"))
        except (AttributeError, ValueError) as exc:
            raise RuntimeError("The target did not return this agent's collection configuration.") from exc
    finally:
        client_engine.close_dispatcher()


def configuration_command(args):
    if getattr(args, "remote", None):
        patch = cli_config_patch(args) if args.config_action == "set" else None
        if args.config_action == "set" and not args.community:
            raise ValueError("Remote config set requires --community with the agent's write community.")
        document = asyncio.run(remote_configuration(
            args.remote, args.community or "public", args.version, patch, args.timeout, args.encryption_config,
        ))
    else:
        path = (args.config or DEFAULT_CONFIG_FILE).expanduser().resolve()
        if args.config_action == "init":
            if path.exists():
                raise FileExistsError(f"Configuration already exists: {path}; use config set to modify it.")
            document = config_document(AgentConfig(port=1161))
            write_json_atomic(path, document)
        elif args.config_action == "show":
            document = read_config_document(path)
            config_from_document(document, path.parent)
        else:
            update_config_file(path, cli_config_patch(args))
            document = read_config_document(path)
    print(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Agent JSON configuration file")
    parser.add_argument("--listen-address", help="IPv4 address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, help="UDP port (default: 161; use 1161 without binding privileges)")
    parser.add_argument("--manager", action="append", help="Allowed manager IPv4/CIDR; repeat to allow more than one")
    parser.add_argument("--sample-interval", type=float, help="Host sampling interval in seconds (default: 1)")
    parser.add_argument("--state-file", type=Path, help="Persist writable system values to this JSON file")
    parser.add_argument("--list-oids", action="store_true", help="Print the local OID snapshot and exit without listening")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    commands = parser.add_subparsers(dest="command")
    help_command = commands.add_parser("help", help="List Agent commands/options and describe a topic")
    help_command.add_argument("topic", nargs="*", help="Examples: config set, mib")
    tree = commands.add_parser("mib", help="Show the shared MIB structure and optionally local collected values")
    tree.add_argument("--group", choices=tuple(COLLECTION_GROUPS) + ("control",), help="Filter by collection family")
    tree.add_argument("--root", help="Filter by numeric OID subtree")
    tree.add_argument("--values", action="store_true", help="Collect and display available local MIB instances")
    tree.add_argument("--config", type=Path, default=argparse.SUPPRESS, help="Agent JSON settings")
    configuration = commands.add_parser("config", help="View/change configuration without a text editor")
    actions = configuration.add_subparsers(dest="config_action", required=True)
    for action in ("init", "show", "set"):
        explanations = {"init": "Create a new JSON configuration without overwriting an existing file",
                        "show": "Display local configuration or a remote Agent's sampling settings",
                        "set": "Validate and atomically persist configuration updates; sampling reloads live"}
        command = actions.add_parser(action, help=explanations[action], description=explanations[action])
        command.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                             help="JSON file (default: snmp_agent_config.json)")
        if action != "init":
            command.add_argument("--remote", help="Remote agent IPv4/hostname[:port] or udp:host:port")
            command.add_argument("--community", help="Read community for show; write community for set")
            command.add_argument("--version", choices=("1", "2c"), default="2c", help="Remote SNMP version (default: 2c)")
            command.add_argument("--timeout", type=float, default=2.0, help="Remote request timeout in seconds")
            command.add_argument("--encryption-config", type=Path, help="RSA encryption profile for remote requests")
        if action == "set":
            command.add_argument("--sample-interval", type=float, default=argparse.SUPPRESS,
                                 help="Default collection interval in seconds (1, 10, 120, etc.)")
            command.add_argument("--interval", dest="metric_interval", nargs=2, action="append", default=[],
                                 metavar=("METRIC", "SECONDS"), help="Override one metric's collection interval")
            command.add_argument("--clear-interval", action="append", default=[], choices=tuple(COLLECTION_GROUPS),
                                 help="Remove a metric override so it inherits sample_interval")
            command.add_argument("--set", dest="assignment", nargs=2, action="append", default=[],
                                 metavar=("FIELD", "VALUE"), help="Set a local config field; JSON arrays/objects supported")
            command.add_argument("--encryption", dest="encryption_mode", choices=("on", "off"),
                                 help="Enable/disable encrypted traffic in the local Agent configuration")
            command.add_argument("--rsa-private-key", type=Path, help="Explicit private PEM for incoming packet decryption")
            command.add_argument("--rsa-peer-public-key", type=Path, help="Optional peer public PEM; default: derive from private key")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "help":
        try:
            show_command_help(parser, args.topic)
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if args.command == "mib":
        try:
            print(mib.render_mib_tree(args.group, args.root))
        except ValueError as exc:
            parser.error(str(exc))
        if not args.values:
            return 0
    if args.command == "config":
        try:
            return configuration_command(args)
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
    try:
        agent_config = load_config(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        metrics = HostMetrics(agent_config, config_path=args.config)
        if args.list_oids or args.command == "mib":
            for oid in metrics.sorted_oids:
                if args.command == "mib":
                    entry = mib.get_mib_object(mib.oid_string(oid))
                    if entry is None or (args.group and entry.group != args.group and mib.group_for_oid(mib.oid_string(oid)) != args.group):
                        continue
                    if args.root and not mib.oid_string(oid).lstrip(".").startswith(args.root.lstrip(".").rstrip(".") + "."):
                        continue
                value = metrics.snapshot[oid]
                symbol = f"{entry.name}: " if args.command == "mib" else ""
                print(f"{symbol}.{'.'.join(map(str, oid))} = {value.__class__.__name__}: {value.prettyPrint()}")
            return 0
        asyncio.run(serve(agent_config, metrics))
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user.")
    except (OSError, ValueError, psutil.Error) as exc:
        LOGGER.error("Agent error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
