"""Shared, portable MIB registry used by the Agent and Manager.

Standard objects live in MIB-2/UCD branches. Metrics without a portable standard
object live under the RFC 5612 documentation enterprise used by this lab Agent.
NumericString gauges preserve large/fractional values without pretending they
are cumulative Counter64 objects.
"""

from dataclasses import dataclass

from snmp_protocol import COLLECTION_CONFIG_OID, COLLECTION_GROUPS, COLLECTION_TABLE, EXAMPLE_ROOT


SYSTEM = (1, 3, 6, 1, 2, 1, 1)
IF_TABLE = (1, 3, 6, 1, 2, 1, 2, 2, 1)
IFX_TABLE = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)
IP_ADDRESS_TABLE = (1, 3, 6, 1, 2, 1, 4, 20, 1)
HR_SYSTEM = (1, 3, 6, 1, 2, 1, 25, 1)
STORAGE = (1, 3, 6, 1, 2, 1, 25, 2, 3, 1)
CPU_LOAD = (1, 3, 6, 1, 2, 1, 25, 3, 3, 1, 2)
PROCESS_TABLE = (1, 3, 6, 1, 2, 1, 25, 4, 2, 1)
PROCESS_PERF = (1, 3, 6, 1, 2, 1, 25, 5, 1, 1)
UCD = (1, 3, 6, 1, 4, 1, 2021)
UCD_DISKIO = UCD + (13, 15, 1, 1)
HOST = EXAMPLE_ROOT + (4, 1)
MEMORY = EXAMPLE_ROOT + (4, 2)
CPU = EXAMPLE_ROOT + (4, 3)
DISKIO = EXAMPLE_ROOT + (4, 4, 1)
FILESYSTEM = EXAMPLE_ROOT + (4, 4, 2)
PROCESSES = EXAMPLE_ROOT + (4, 5, 1)
CONNECTIONS = EXAMPLE_ROOT + (4, 6)
SENSORS = EXAMPLE_ROOT + (4, 7)
ADDRESSES = EXAMPLE_ROOT + (4, 8, 1)
SERVICES = EXAMPLE_ROOT + (4, 9, 1)


def oid_string(value):
    return "." + ".".join(map(str, value))


@dataclass(frozen=True)
class MibObject:
    oid: tuple
    name: str
    description: str
    data_type: str
    group: str
    kind: str = "gauge"
    unit: str = ""
    index_label: str = ""
    optional: bool = False
    writable: bool = False
    agent_supported: bool = True

    @property
    def template(self):
        return oid_string(self.oid) + (".{index}" if self.index_label else "")

    def matches(self, number):
        return number[:len(self.oid)] == self.oid and len(number) > len(self.oid) if self.index_label else number == self.oid

    def metric(self):
        result = {"name": self.description, "kind": self.kind, "unit": self.unit,
                  "axis": self.description + (f" ({self.unit})" if self.unit else "")}
        if self.data_type in ("Counter32", "Counter64"):
            result["counter_bits"] = 64 if self.data_type == "Counter64" else 32
        return result


OBJECTS = []


def scalar(root, column, name, description, data_type, group, kind="gauge", unit="", **options):
    OBJECTS.append(MibObject(root + (column, 0), name, description, data_type, group, kind, unit, **options))


def table(root, rows, group, index_label, optional=False):
    for column, name, description, data_type, kind, unit in rows:
        OBJECTS.append(MibObject(root + (column,), name, description, data_type, group, kind, unit,
                                 index_label, optional))


for column, name, description, data_type, kind, unit in (
    (1, "sysDescr", "System and OS description", "OctetString", "text", ""),
    (2, "sysObjectID", "Agent implementation identifier", "ObjectIdentifier", "text", ""),
    (3, "sysUpTime", "Agent uptime", "TimeTicks", "timeticks", "hours"),
    (4, "sysContact", "Administrator contact", "OctetString", "text", ""),
    (5, "sysName", "Reported hostname", "OctetString", "text", ""),
    (6, "sysLocation", "Device location", "OctetString", "text", ""),
    (7, "sysServices", "Reported host service layers", "Integer32", "gauge", "bitmask"),
):
    scalar(SYSTEM, column, name, description, data_type, "system", kind, unit, writable=column in (4, 5, 6))
scalar((1, 3, 6, 1, 2, 1, 2), 1, "ifNumber", "Number of interfaces", "Integer32", "interfaces", unit="interfaces")
table(IF_TABLE, (
    (1, "ifIndex", "Interface index", "Integer32", "gauge", ""),
    (2, "ifDescr", "Interface description", "OctetString", "text", ""),
    (3, "ifType", "Interface type", "Integer32", "gauge", "type code"),
    (4, "ifMtu", "Interface MTU", "Integer32", "gauge", "bytes"),
    (5, "ifSpeed", "Interface speed", "Gauge32", "speed_bps", "Mbps"),
    (6, "ifPhysAddress", "Interface MAC address", "OctetString", "text", ""),
    (7, "ifAdminStatus", "Administrative status", "Integer32", "status_admin", "state"),
    (8, "ifOperStatus", "Operational status", "Integer32", "status_oper", "state"),
    (9, "ifLastChange", "Uptime at last interface state change", "TimeTicks", "timeticks", "hours"),
    (10, "ifInOctets", "Incoming network throughput", "Counter32", "counter_octets", "Mbps"),
    (13, "ifInDiscards", "Incoming discard rate", "Counter32", "counter_discards", "discards/s"),
    (14, "ifInErrors", "Incoming error rate", "Counter32", "counter_errors", "errors/s"),
    (16, "ifOutOctets", "Outgoing network throughput", "Counter32", "counter_octets", "Mbps"),
    (19, "ifOutDiscards", "Outgoing discard rate", "Counter32", "counter_discards", "discards/s"),
    (20, "ifOutErrors", "Outgoing error rate", "Counter32", "counter_errors", "errors/s"),
), "interfaces", "interface index")
# Known external-agent options are retained without fabricating unicast data.
for column, name in ((11, "ifInUcastPkts"), (17, "ifOutUcastPkts")):
    OBJECTS.append(MibObject(IF_TABLE + (column,), name, "Unicast-only packet rate (external agents)",
                             "Counter32", "interfaces", "counter_packets", "packets/s", "interface index",
                             agent_supported=False))
table(IFX_TABLE, (
    (1, "ifName", "Interface name", "OctetString", "text", ""),
    (6, "ifHCInOctets", "Incoming network throughput (64-bit)", "Counter64", "counter_octets", "Mbps"),
    (10, "ifHCOutOctets", "Outgoing network throughput (64-bit)", "Counter64", "counter_octets", "Mbps"),
    (15, "ifHighSpeed", "High-capacity interface speed", "Gauge32", "gauge", "Mbps"),
), "interfaces", "interface index")
table(EXAMPLE_ROOT + (2, 1), (
    (1, "totalPacketsReceived", "Total incoming packet rate", "Counter32", "counter_packets", "packets/s"),
    (2, "totalPacketsSent", "Total outgoing packet rate", "Counter32", "counter_packets", "packets/s"),
), "interfaces", "interface index")
table(IP_ADDRESS_TABLE, (
    (1, "ipAdEntAddr", "IPv4 address", "IpAddress", "text", ""),
    (2, "ipAdEntIfIndex", "IPv4 interface index", "Integer32", "gauge", ""),
    (3, "ipAdEntNetMask", "IPv4 netmask", "IpAddress", "text", ""),
), "interfaces", "IPv4 address")
scalar(HR_SYSTEM, 1, "hrSystemUptime", "Host uptime", "TimeTicks", "system", "timeticks", "hours")
scalar(HR_SYSTEM, 5, "hrSystemNumUsers", "Logged-in sessions", "Gauge32", "system", unit="sessions")
scalar(HR_SYSTEM, 6, "hrSystemProcesses", "Running process count", "Gauge32", "system", unit="processes")
scalar((1, 3, 6, 1, 2, 1, 25, 2), 2, "hrMemorySize", "Total physical memory", "Integer32", "memory", "memory_kib", "MiB")
table(STORAGE, (
    (1, "hrStorageIndex", "Storage index", "Integer32", "gauge", ""),
    (2, "hrStorageType", "Storage type", "ObjectIdentifier", "text", ""),
    (3, "hrStorageDescr", "Storage description", "OctetString", "text", ""),
    (4, "hrStorageAllocationUnits", "Storage allocation unit size", "Integer32", "gauge", "bytes"),
    (5, "hrStorageSize", "Total storage allocation units", "Integer32", "gauge", "units"),
    (6, "hrStorageUsed", "Used storage allocation units", "Integer32", "gauge", "units"),
), "storage", "storage index")
OBJECTS.append(MibObject(CPU_LOAD, "hrProcessorLoad", "Logical processor CPU load", "Integer32", "cpu",
                         "percent", "%", "processor index"))
table(PROCESS_TABLE, (
    (1, "hrSWRunIndex", "Process ID", "Integer32", "gauge", ""),
    (2, "hrSWRunName", "Process name", "OctetString", "text", ""),
    (4, "hrSWRunPath", "Process executable path", "OctetString", "text", ""),
    (6, "hrSWRunType", "Process type", "Integer32", "gauge", "type code"),
    (7, "hrSWRunStatus", "Process run status", "Integer32", "gauge", "state code"),
), "processes", "process ID", optional=True)
table(PROCESS_PERF, (
    (1, "hrSWRunPerfCPU", "Accumulated process CPU time", "Integer32", "timeticks", "hours"),
    (2, "hrSWRunPerfMem", "Process resident memory", "Integer32", "memory_kib", "MiB"),
), "processes", "process ID", optional=True)
for column, name, description in ((3, "memTotalSwap", "Total swap"), (4, "memAvailSwap", "Free swap"),
                                  (5, "memTotalReal", "Total RAM"), (6, "memAvailReal", "Available RAM"),
                                  (14, "memBuffer", "RAM buffers"), (15, "memCached", "RAM cache")):
    scalar(UCD + (4,), column, name, description, "Integer32", "memory", "memory_kib", "MiB", optional=column in (14, 15))
scalar(UCD + (11,), 11, "ssCpuIdle", "Rolling CPU idle", "Integer32", "cpu", "percent", "%")
table(UCD + (10, 1), (
    (1, "laIndex", "Load-average index", "Integer32", "gauge", ""),
    (2, "laNames", "Load-average period", "OctetString", "text", ""),
    (3, "laLoad", "System load average", "NumericString", "gauge", "load"),
), "load", "load index", optional=True)
table(UCD_DISKIO, (
    (1, "diskIOIndex", "Disk I/O index", "Integer32", "gauge", ""),
    (2, "diskIODevice", "Disk device", "OctetString", "text", ""),
    (3, "diskIONRead", "Disk read throughput (32-bit)", "Counter32", "counter_bytes", "MiB/s"),
    (4, "diskIONWritten", "Disk write throughput (32-bit)", "Counter32", "counter_bytes", "MiB/s"),
    (5, "diskIOReads", "Disk read operations", "Counter32", "counter_generic", "operations/s"),
    (6, "diskIOWrites", "Disk write operations", "Counter32", "counter_generic", "operations/s"),
    (9, "diskIONReadX", "Disk read throughput (64-bit)", "Counter64", "counter_bytes", "MiB/s"),
    (10, "diskIONWrittenX", "Disk write throughput (64-bit)", "Counter64", "counter_bytes", "MiB/s"),
), "diskio", "disk index", optional=True)

for column, name, description, data_type, unit in (
    (1, "osName", "Operating system name", "OctetString", ""),
    (2, "osRelease", "Operating system release", "OctetString", ""),
    (3, "osVersion", "Operating system version", "OctetString", ""),
    (4, "architecture", "Machine architecture", "OctetString", ""),
    (5, "processor", "Processor description", "OctetString", ""),
    (6, "pythonVersion", "Agent Python version", "OctetString", ""),
    (7, "bootTime", "Host boot time (Unix seconds)", "NumericString", "seconds"),
):
    scalar(HOST, column, name, description, data_type, "system", "text" if not unit else "gauge", unit)
for column, name, description in (
    (1, "totalBytes", "Total physical RAM"), (2, "availableBytes", "Available physical RAM"),
    (3, "usedBytes", "Physical RAM used (total minus available)"), (4, "freeBytes", "OS free RAM"),
    (5, "buffersBytes", "RAM buffers"), (6, "cachedBytes", "RAM cache"),
    (7, "activeBytes", "Active RAM"), (8, "inactiveBytes", "Inactive RAM"),
    (10, "swapTotalBytes", "Total swap"), (11, "swapUsedBytes", "Used swap"), (12, "swapFreeBytes", "Free swap"),
):
    scalar(MEMORY, column, name, description, "NumericString", "memory", "bytes_gauge", "GiB", optional=column in (4, 5, 6, 7, 8))
scalar(MEMORY, 9, "ramUsagePercent", "Physical RAM utilization", "NumericString", "memory", "percent", "%")
scalar(MEMORY, 13, "swapUsagePercent", "Swap utilization", "NumericString", "memory", "percent", "%")
for column, name, description, data_type, unit in (
    (1, "logicalProcessors", "Logical processor count", "Gauge32", "processors"),
    (2, "physicalCores", "Physical CPU core count", "Gauge32", "cores"),
    (3, "frequencyCurrent", "Current CPU frequency", "NumericString", "MHz"),
    (4, "frequencyMin", "Minimum CPU frequency", "NumericString", "MHz"),
    (5, "frequencyMax", "Maximum CPU frequency", "NumericString", "MHz"),
):
    scalar(CPU, column, name, description, data_type, "cpu", unit=unit, optional=column != 1)
for column, name in ((6, "user"), (7, "system"), (8, "idle"), (9, "nice"), (10, "iowait"), (11, "irq"), (12, "softirq"), (13, "steal")):
    scalar(CPU, column, name + "Percent", "CPU " + name + " (last sampling interval)", "NumericString", "cpu", "percent", "%", optional=True)
for column, name in ((14, "contextSwitches"), (15, "interrupts"), (16, "softInterrupts"), (17, "syscalls")):
    scalar(CPU, column, name, name + " rate", "Counter64", "cpu", "counter_generic", "events/s", optional=True)
table(DISKIO, (
    (1, "index", "Disk index", "Integer32", "gauge", ""),
    (2, "name", "Disk name", "OctetString", "text", ""),
    (3, "readBytes", "Disk read throughput", "Counter64", "counter_bytes", "MiB/s"),
    (4, "writeBytes", "Disk write throughput", "Counter64", "counter_bytes", "MiB/s"),
    (5, "readCount", "Disk read operation rate", "Counter64", "counter_generic", "operations/s"),
    (6, "writeCount", "Disk write operation rate", "Counter64", "counter_generic", "operations/s"),
    (7, "readTime", "Accumulated read-time rate", "Counter64", "counter_generic", "milliseconds/s"),
    (8, "writeTime", "Accumulated write-time rate", "Counter64", "counter_generic", "milliseconds/s"),
    (9, "busyTime", "Accumulated disk busy-time rate", "Counter64", "counter_generic", "milliseconds/s"),
    (10, "readMerged", "Merged disk read rate", "Counter64", "counter_generic", "operations/s"),
    (11, "writeMerged", "Merged disk write rate", "Counter64", "counter_generic", "operations/s"),
), "diskio", "disk index", optional=True)
table(FILESYSTEM, (
    (1, "index", "Filesystem index", "Integer32", "gauge", ""),
    (2, "device", "Filesystem device", "OctetString", "text", ""),
    (3, "mountpoint", "Filesystem mount point", "OctetString", "text", ""),
    (4, "type", "Filesystem type", "OctetString", "text", ""),
    (5, "options", "Filesystem mount options", "OctetString", "text", ""),
    (6, "totalBytes", "Filesystem capacity", "NumericString", "bytes_gauge", "GiB"),
    (7, "usedBytes", "Filesystem used space", "NumericString", "bytes_gauge", "GiB"),
    (8, "freeBytes", "Filesystem free space", "NumericString", "bytes_gauge", "GiB"),
    (9, "usagePercent", "Filesystem utilization", "NumericString", "percent", "%"),
    (10, "storageIndex", "HOST-RESOURCES storage row", "Integer32", "gauge", ""),
), "storage", "filesystem index", optional=True)
table(PROCESSES, (
    (1, "pid", "Process ID", "Integer32", "gauge", ""),
    (2, "parentPid", "Parent process ID", "Integer32", "gauge", ""),
    (3, "name", "Process name", "OctetString", "text", ""),
    (4, "username", "Process owner", "OctetString", "text", ""),
    (5, "status", "Process status", "OctetString", "text", ""),
    (6, "threads", "Process thread count", "Gauge32", "gauge", "threads"),
    (7, "cpuPercent", "Process CPU utilization (100% per logical CPU)", "NumericString", "gauge", "%"),
    (8, "rssBytes", "Process resident RAM", "NumericString", "bytes_gauge", "GiB"),
    (9, "vmsBytes", "Process virtual memory", "NumericString", "bytes_gauge", "GiB"),
    (10, "createTime", "Process creation time (Unix seconds)", "NumericString", "gauge", "seconds"),
    (11, "exe", "Process executable", "OctetString", "text", ""),
), "processes", "process ID", optional=True)
for column, name in enumerate(("tcpTotal", "tcpEstablished", "tcpListening", "tcpTimeWait", "tcpCloseWait", "tcpSynSent", "udpTotal"), 1):
    scalar(CONNECTIONS, column, name, name + " socket count", "Gauge32", "connections", unit="sockets", optional=True)
OBJECTS.append(MibObject((1, 3, 6, 1, 2, 1, 6, 9, 0), "tcpCurrEstab", "Established and CLOSE_WAIT TCP connections", "Gauge32", "connections", unit="sockets", optional=True))
scalar(SENSORS, 1, "batteryPercent", "Battery charge", "NumericString", "sensors", "percent", "%", optional=True)
scalar(SENSORS, 2, "batterySecondsLeft", "Battery seconds remaining (-1 unknown, -2 unlimited)", "NumericString", "sensors", unit="seconds", optional=True)
scalar(SENSORS, 3, "batteryPlugged", "Battery external power (1=yes, 0=no)", "Gauge32", "sensors", optional=True)
table(SENSORS + (4,), (
    (1, "index", "Temperature sensor index", "Integer32", "gauge", ""),
    (2, "hardware", "Temperature sensor hardware", "OctetString", "text", ""),
    (3, "label", "Temperature sensor label", "OctetString", "text", ""),
    (4, "current", "Current temperature", "NumericString", "gauge", "C"),
    (5, "high", "High temperature threshold", "NumericString", "gauge", "C"),
    (6, "critical", "Critical temperature threshold", "NumericString", "gauge", "C"),
), "sensors", "temperature sensor index", optional=True)
table(SENSORS + (5,), (
    (1, "index", "Fan index", "Integer32", "gauge", ""),
    (2, "hardware", "Fan hardware", "OctetString", "text", ""),
    (3, "label", "Fan label", "OctetString", "text", ""),
    (4, "current", "Fan speed", "Gauge32", "gauge", "RPM"),
), "sensors", "fan index", optional=True)
table(ADDRESSES, (
    (1, "index", "Address index", "Integer32", "gauge", ""),
    (2, "interfaceName", "Address interface", "OctetString", "text", ""),
    (3, "interfaceIndex", "Address interface index", "Integer32", "gauge", ""),
    (4, "address", "IPv4/IPv6 address", "OctetString", "text", ""),
    (5, "netmask", "Address netmask", "OctetString", "text", ""),
    (6, "family", "Address family (4=IPv4, 6=IPv6)", "Integer32", "gauge", ""),
    (7, "broadcast", "Broadcast address", "OctetString", "text", ""),
    (8, "pointToPoint", "Point-to-point peer", "OctetString", "text", ""),
), "interfaces", "address index")
table(SERVICES, (
    (1, "index", "Windows service index", "Integer32", "gauge", ""),
    (2, "name", "Windows service name", "OctetString", "text", ""),
    (3, "displayName", "Windows service display name", "OctetString", "text", ""),
    (4, "status", "Windows service status", "OctetString", "text", ""),
    (5, "startType", "Windows service startup type", "OctetString", "text", ""),
    (6, "pid", "Windows service PID", "Integer32", "gauge", ""),
    (7, "username", "Windows service account", "OctetString", "text", ""),
    (8, "description", "Windows service description", "OctetString", "text", ""),
    (9, "binaryPath", "Windows service binary path", "OctetString", "text", ""),
), "services", "service index", optional=True)
OBJECTS.append(MibObject(COLLECTION_CONFIG_OID, "collectionConfiguration", "Writable sampling configuration JSON",
                         "OctetString", "control", "text", writable=True))
table(COLLECTION_TABLE, (
    (1, "name", "Collection group name", "OctetString", "text", ""),
    (2, "interval", "Effective collection period", "NumericString", "gauge", "seconds"),
    (3, "lastSample", "Last collection time since Agent startup", "NumericString", "gauge", "seconds"),
    (4, "sampleCount", "Completed collection count", "Counter32", "counter_generic", "collections/s"),
), "control", "collection group index")
OBJECTS = tuple(OBJECTS)
SCALARS = {entry.oid: entry for entry in OBJECTS if not entry.index_label}
TABLE_COLUMNS = {entry.oid: entry for entry in OBJECTS if entry.index_label}


BRANCHES = {
    (1, 3, 6, 1): "internet", (1, 3, 6, 1, 2): "mgmt", (1, 3, 6, 1, 2, 1): "mib-2",
    SYSTEM: "system", (1, 3, 6, 1, 2, 1, 2): "interfaces", IF_TABLE[:-1]: "ifTable", IF_TABLE: "ifEntry",
    (1, 3, 6, 1, 2, 1, 4): "ip", IP_ADDRESS_TABLE[:-1]: "ipAddrTable", IP_ADDRESS_TABLE: "ipAddrEntry",
    (1, 3, 6, 1, 2, 1, 6): "tcp", (1, 3, 6, 1, 2, 1, 25): "hostResources", HR_SYSTEM: "hrSystem",
    (1, 3, 6, 1, 2, 1, 25, 2): "hrStorage", STORAGE[:-1]: "hrStorageTable", STORAGE: "hrStorageEntry",
    (1, 3, 6, 1, 2, 1, 25, 3): "hrDevice", CPU_LOAD[:-1]: "hrProcessorEntry",
    (1, 3, 6, 1, 2, 1, 25, 4): "hrSWRun", PROCESS_TABLE[:-1]: "hrSWRunTable", PROCESS_TABLE: "hrSWRunEntry",
    (1, 3, 6, 1, 2, 1, 25, 5): "hrSWRunPerf", PROCESS_PERF: "hrSWRunPerfEntry",
    (1, 3, 6, 1, 2, 1, 31): "ifMIB", IFX_TABLE: "ifXEntry",
    (1, 3, 6, 1, 4): "private", (1, 3, 6, 1, 4, 1): "enterprises", UCD: "ucdSnmpAgent",
    UCD + (4,): "memory", UCD + (10,): "loadAverage", UCD + (11,): "cpu", UCD_DISKIO: "diskIOEntry",
    EXAMPLE_ROOT[:-1]: "documentationEnterprise", EXAMPLE_ROOT: "hookah",
    EXAMPLE_ROOT + (2, 1): "packetTotals", EXAMPLE_ROOT + (3,): "collectionControl",
    COLLECTION_TABLE: "collectionEntry", EXAMPLE_ROOT + (4,): "deviceMetrics", HOST: "hostInformation",
    MEMORY: "memoryDetails", CPU: "cpuDetails", DISKIO: "diskIOEntry", FILESYSTEM: "filesystemEntry",
    PROCESSES: "processEntry", CONNECTIONS: "connectionSummary", SENSORS: "sensors",
    SENSORS + (4,): "temperatureEntry", SENSORS + (5,): "fanEntry", ADDRESSES: "addressEntry", SERVICES: "serviceEntry",
}
BRANCHES.update({
    (1, 3, 6, 1, 2, 1, 25, 3, 3): "hrProcessorTable",
    (1, 3, 6, 1, 2, 1, 25, 5, 1): "hrSWRunPerfTable",
    (1, 3, 6, 1, 2, 1, 31, 1): "ifMIBObjects", (1, 3, 6, 1, 2, 1, 31, 1, 1): "ifXTable",
    UCD + (10, 1): "laEntry", UCD + (13,): "extensions", UCD + (13, 15): "diskIO",
    UCD + (13, 15, 1): "diskIOTable", EXAMPLE_ROOT + (2,): "packetMetrics",
    EXAMPLE_ROOT + (4, 4): "storageAndDiskIO", EXAMPLE_ROOT + (4, 5): "processMetrics",
    EXAMPLE_ROOT + (4, 8): "networkAddresses", EXAMPLE_ROOT + (4, 9): "windowsServices",
})


def get_mib_object(oid):
    try:
        number = tuple(int(part) for part in str(oid).strip().lstrip(".").split("."))
    except ValueError:
        return None
    if number in SCALARS:
        return SCALARS[number]
    for length in range(len(number) - 1, 0, -1):
        if number[:length] in TABLE_COLUMNS:
            return TABLE_COLUMNS[number[:length]]
    return None


def group_for_oid(oid):
    value = str(oid).strip().lstrip(".")
    if value in ("1.3.6.1.2.1.25.2.3", "1.3.6.1.2.1.25.3.3.1.2"):
        return "memory" if value.endswith("25.2.3") else "cpu"
    entry = get_mib_object(oid)
    if entry is None or entry.group == "control":
        return None
    if entry.oid[:len(STORAGE)] == STORAGE:
        index = int(value.split(".")[-1])
        return "memory" if index in (1, 2) else "storage"
    if entry.name in ("ifInOctets", "ifHCInOctets"):
        return "ifInOctets"
    if entry.name in ("ifOutOctets", "ifHCOutOctets"):
        return "ifOutOctets"
    return entry.group


def catalog_extensions(existing, start=26):
    existing = set(existing)
    result = []
    for entry in OBJECTS:
        if entry.template in existing or not entry.agent_supported or entry.group == "control":
            continue
        item = {"number": str(start + len(result)), "name": f"{entry.description} ({entry.name})",
                "template": entry.template, "platform": "OS/permissions dependent" if entry.optional else "Windows/Linux",
                "use": entry.description, "section": entry.group, "optional": entry.optional}
        if entry.index_label:
            item["index_label"] = entry.index_label
        result.append(item)
        existing.add(entry.template)
    return result


def render_mib_tree(group=None, root=None):
    prefix = tuple(int(part) for part in root.strip().lstrip(".").split(".")) if root else (1, 3, 6, 1)
    if group and group not in COLLECTION_GROUPS and group != "control":
        raise ValueError("Unknown MIB group: " + group)
    entries = [entry for entry in OBJECTS if (not group or entry.group == group or
               (group in ("ifInOctets", "ifOutOctets") and entry.name in (
                   ("ifInOctets", "ifHCInOctets") if group == "ifInOctets" else ("ifOutOctets", "ifHCOutOctets"))))
               and entry.oid[:len(prefix)] == prefix]
    if not entries:
        return "No MIB objects match the selected group/root."
    paths = {prefix}
    leaves = {}
    for entry in entries:
        displayed = entry.oid if entry.index_label or len(entry.oid) <= len(prefix) else entry.oid[:-1]
        for length in range(len(prefix) + 1, len(displayed) + 1):
            paths.add(displayed[:length])
        leaves[displayed] = entry
    children = {}
    for path in sorted(paths):
        if path != prefix:
            children.setdefault(path[:-1], []).append(path)
    lines = [f"{BRANCHES.get(prefix, 'MIB')} ({oid_string(prefix)})"]

    def draw(parent, indent=""):
        nodes = children.get(parent, [])
        for index, path in enumerate(nodes):
            last = index == len(nodes) - 1
            entry = leaves.get(path)
            if entry:
                instance = ".{index}" if entry.index_label else ".0"
                flags = "; optional" if entry.optional else ""
                flags += "; external agent only" if not entry.agent_supported else ""
                flags += "; writable" if entry.writable else ""
                text = f"{entry.name}{instance} ({path[-1]}) [{entry.data_type}; {entry.group}{'; ' + entry.unit if entry.unit else ''}{flags}]"
            else:
                text = f"{BRANCHES.get(path, str(path[-1]))} ({path[-1]})"
            lines.append(indent + ("`-- " if last else "|-- ") + text)
            draw(path, indent + ("    " if last else "|   "))
    draw(prefix)
    lines.append("Indexed objects use WALK to discover {index}; optional objects appear when the OS and permissions provide data.")
    return "\n".join(lines)


def walk_roots(group=None):
    """Minimal subtree roots covering the selected registry group."""
    entries = [entry for entry in OBJECTS if entry.agent_supported and
               (group is None or entry.group == group or
                (group == "ifInOctets" and entry.name in ("ifInOctets", "ifHCInOctets")) or
                (group == "ifOutOctets" and entry.name in ("ifOutOctets", "ifHCOutOctets")))]
    roots = sorted({entry.oid[:-1] if entry.index_label else entry.oid[:-2] for entry in entries}, key=lambda item: (len(item), item))
    selected = []
    for root in roots:
        if not any(root[:len(parent)] == parent for parent in selected):
            selected.append(root)
    return tuple(selected)
