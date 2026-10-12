# Hookah Agent 1.0.0

## Packaged CLI entrypoints

For end-user deployment, extract `Hookah-Agent-Linux-1.0.0.zip` or
`Hookah-Agent-Windows-1.0.0.zip`. Its visible files are `Hookah-Agent.sh` or
`Hookah-Agent.exe`, `README.md`, `HELP.md`, and `config.json`. Run the launcher
directly after extraction; portable Python and the installed dependencies are
included. `setup` is only an optional offline check. Code, dependencies, advanced guides,
and service scripts are grouped under `.hookah/`. The examples below also cover
direct use from the developer source project; in a package, replace the script
entrypoint with the public launcher and use the visible `config.json` profile.

`snmp_agent.py` runs on the **device being monitored**. It collects that device's
system information, interface traffic, CPU load, and physical-memory usage, then
answers SNMP requests from the manager on your local network.

The communication flow matches `snmp_data_collector_v12.py`:

```text
Manager -- GET / GETNEXT / GETBULK / SET --> Device agent (UDP 161)
Manager <-- response containing local metrics -- Device agent
```

The manager's Continuous OID capture option controls polling and CSV storage.
Responses go back to the IP and source port of each request; you do not need to
configure a manager receiving port. `allowed_managers` specifies which manager
source IP addresses can query the agent. This is request/response operation;
the current manager has no trap receiver.

## Requirements

- Python **3.10 or newer** on the monitored host.
- Dependencies in `requirements-agent.txt` (PySNMP and psutil).
- Network connectivity from the manager to the agent's chosen **UDP** port.
- Permission to bind UDP 161, or use an unprivileged port such as 1161.

The portable collection backend supports Linux and Windows. The service example
below is for Linux/systemd. Integration tests run against real Net-SNMP clients
on Linux.

For a native **Windows 10/11 auto-start service**, use `WINDOWS-AGENT.md` and
`windows/install-agent.ps1`. A transferable package can be built with
`python build_agent_package.py`. `make release` builds the verified versioned
Agent and Manager distributions together. The Agent ZIP includes this guide,
its editable/default JSON profiles, shared guides, requirements, and launchers.

Optional authenticated **RSA/AES-GCM** traffic encryption is documented in
`ENCRYPTION.md`. The Manager generates the PEM keys; the Agent's configuration
explicitly references the copied private PEM for incoming packet decryption.
The Manager displays a Shisha ASCII banner with **Hookah** below it at startup.
The current startup screen uses a **HOOKAH** heading and dot-art Shisha, credited
to **0moorii**. The shared extended MIB/tree, 180 OID choices, named browsing,
and descriptive `help` commands are documented in `MIB.md`.

## Quick start on the monitored device

Copy `snmp_agent.py`, `hookah_version.py`, `snmp_protocol.py`, `snmp_mib.py`, `snmp_cli_help.py`, `snmp_crypto.py`, `requirements-agent.txt`, and
`snmp_agent_config.json` to the device. In that directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-agent.txt
source .venv/bin/activate
chmod +x snmp_agent.py
```

On Windows, use `py -3 -m venv .venv` and `.venv\Scripts\python.exe` for subsequent
Python commands.

Packaged `Hookah-Agent.sh` and `Hookah-Agent.exe` select the private
`.hookah/environment` and the visible `config.json`, including when invoked
from another directory. No dependency installation is needed. Both public
launchers report **1.0.0** with `--version`.

The supplied editable `snmp_agent_config.json` uses UDP 1161 and initially permits
loopback queries. Configure the LAN manager without opening a text editor. For
manager `192.168.1.10` and monitored device `192.168.1.20`:

```bash
./snmp_agent.py config set --config snmp_agent_config.json \
  --set allowed_managers '["127.0.0.1/32", "192.168.1.10/32"]'
./snmp_agent.py config show --config snmp_agent_config.json
```

A typical configuration looks like:

```json
{
  "listen_address": "0.0.0.0",
  "port": 1161,
  "read_community": "public",
  "write_community": null,
  "allowed_managers": ["192.168.1.10/32"],
  "sample_interval": 1.0,
  "collection_intervals": {},
  "sys_contact": "Lab administrator",
  "sys_name": null,
  "sys_location": "Local network lab",
  "state_file": null
}
```

Run:

```bash
./snmp_agent.py --config snmp_agent_config.json
```

This example uses UDP 1161, so it can run as an ordinary user. For UDP 161, set
`port` to `161` and use the service installation below, or run with the required
binding permissions. Only one process can occupy a particular IP/UDP port; if
`snmpd` already uses it, choose another port or adjust that service.

For a local-only trial without a configuration file:

```bash
.venv/bin/python snmp_agent.py --listen-address 127.0.0.1 --port 1161
```

The default JSON filename is `snmp_agent_config.json` in the working directory;
when it exists, running `./snmp_agent.py` loads it automatically. Built-in defaults
without a file are community `public`, one-second sampling, UDP 161, and
loopback-only manager access. To allow your LAN manager at startup, add
`--manager 192.168.1.10`. Repeating `--manager` allows multiple addresses or CIDRs
and overrides the configured list. Other CLI overrides are `--port`,
`--listen-address`, `--sample-interval`, and `--state-file`.

Use `--list-oids` to inspect local values and discover interface/processor
indexes without opening a listening socket.

## Collection frequency: seconds only

`sample_interval` is the default sampling period. `collection_intervals` can
override it for individual metric groups. Values are finite, positive **numbers
in seconds**: `1`, `10`, and `120` mean one second, ten seconds, and two minutes.
Fractional seconds are also supported. Strings such as `2m` or `120s`, zero,
negative values, NaN, and infinity are rejected before the configuration changes.

Change all default collection periods:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --sample-interval 10
```

Set incoming octets to one, ten, or 120 seconds:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 1
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 10
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 120
```

For example:

```json
{
  "sample_interval": 1,
  "collection_intervals": {
    "ifInOctets": 120,
    "ifOutOctets": 1,
    "memory": 10
  }
}
```

This example is the scheduling portion of the full configuration. Supported
groups are `system`, `interfaces`, `ifInOctets`, `ifOutOctets`, `cpu`, `memory`,
`load`, `storage`, `diskio`, `processes`, `connections`, `sensors`, and `services`.
Each group's schedule covers all of its indexed entries;
`ifInOctets` also controls `ifHCInOctets`. `interfaces` covers descriptions,
states, errors, discards, and packet totals.

Clear an override to inherit `sample_interval`:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --clear-interval ifInOctets
```

The running agent checks for sampling configuration changes every 0.2 seconds,
independently of collection periods. A 120-second schedule can therefore be
shortened without waiting two minutes or restarting. The next collection is
scheduled relative to the last completed collection. GET/WALK requests return
cached measurements between collections and do not trigger an early collection.

CLI edits validate the full configuration and write it atomically. Existing file
permissions are retained. Sampling fields reload live; changes to listening
addresses, communities, manager permissions, and metadata defaults take effect
on restart. Startup `--sample-interval` is a temporary override; keep sampling
values in the file when using live configuration changes.

### Remotely change configuration through the same agent CLI

Enable a write community on the device:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --set write_community lab-write
```

Restart once after enabling/changing a community. From an allowed manager
machine with the agent code and its dependencies installed:

```bash
./snmp_agent.py config show --remote udp:192.168.1.20:1161 --community public
./snmp_agent.py config set --remote udp:192.168.1.20:1161 --community lab-write \
  --interval ifInOctets 120
./snmp_agent.py config set --remote udp:192.168.1.20:1161 --community lab-write \
  --sample-interval 10
```

These commands use SNMP GET/SET. The read community can inspect sampling settings;
the write community is required to modify them. Remote changes are limited to
sampling settings, are persisted in the **device's** JSON file, and apply live.
The file and its parent directory must be writable by the account running the
agent. The service setup below supplies that directory.

The same local command can be executed over SSH:

```bash
ssh user@192.168.1.20 '/opt/snmp-python-agent/.venv/bin/python /opt/snmp-python-agent/snmp_agent.py config set --config /var/lib/snmp-python-agent/config.json --interval ifInOctets 120'
```

Use `config init --config PATH` to create a fresh configuration on a new device.
It refuses to overwrite an existing file. `--set FIELD VALUE` changes other local
configuration fields; array/object values use JSON syntax.

## Connect with the existing manager

On the manager machine, start `snmp_data_collector_v12.py`. For GET, WALK, or
Continuous OID capture, enter:

```text
Target IP/hostname: udp:192.168.1.20:1161
SNMP version [2c]: 2c
Community string [public]: public
```

With the default UDP 161 port, entering `192.168.1.20` alone is sufficient.
SNMP version `1` is also supported. The community must match the agent's
`read_community` (or its enabled `write_community`).

Useful workflows:

1. **WALK → Interface descriptions**: discover the actual interface index.
2. **Continuous capture → catalog 4 or 5**: incoming/outgoing throughput.
3. **Continuous capture → catalog 15**: average CPU utilization.
4. **GET or Continuous capture → catalog 25**: physical RAM summary/usage.
5. **Plot/statistics from CSV**: visualize the manager's captured data.

The manager also has executable commands. Keep `snmp_protocol.py`, `snmp_mib.py`,
`snmp_cli_help.py`, and `snmp_crypto.py` beside it and
activate the Python environment:

```bash
./snmp_data_collector_v12.py get --target udp:192.168.1.20:1161 \
  --community public --oid .1.3.6.1.2.1.1.1.0
./snmp_data_collector_v12.py capture --target udp:192.168.1.20:1161 \
  --community public --oid .1.3.6.1.2.1.2.2.1.10.2 \
  --sync-agent --duration 120 --output incoming.csv
```

Replace interface index `2` with the discovered index. `--sync-agent` reads the
metric's effective collection period and follows live changes, including while
waiting for a long period. The manager continues checking the period at least
once per second while waiting. Capture duration is bounded even when the period
is 120 seconds. A manual `--interval 1` is also available. Running the manager
without arguments opens its original interactive menu.

You can also verify communication directly from the manager machine:

```bash
snmpget -v2c -c public -On udp:192.168.1.20:1161 .1.3.6.1.2.1.1.1.0
snmpwalk -v2c -c public -On udp:192.168.1.20:1161 .1.3.6.1.2.1.2.2.1.2
snmpwalk -v2c -c public -On udp:192.168.1.20:1161 .1.3.6.1.2.1.25.3.3.1.2
snmpwalk -v2c -c public -On udp:192.168.1.20:1161 .1.3.6.1.2.1.25.2.3
```

For a UFW-managed device firewall, allow the configured manager's source IP
and chosen UDP port, for example:

```bash
sudo ufw allow from 192.168.1.10 to any port 1161 proto udp
```

## Exposed metrics and OIDs

`{index}` is the index discovered by walking the corresponding table.

| Measurement | OID | Manager catalog |
| --- | --- | --- |
| System description | `.1.3.6.1.2.1.1.1.0` | 20 |
| Agent object ID | `.1.3.6.1.2.1.1.2.0` | 21 |
| Agent uptime, hundredths of a second | `.1.3.6.1.2.1.1.3.0` | 13 |
| Contact / name / location | `.1.3.6.1.2.1.1.4.0`, `.5.0`, `.6.0` in the same group | 22–24 |
| Interface descriptions | `.1.3.6.1.2.1.2.2.1.2.{index}` | 1 |
| Administrative / operational state | `.1.3.6.1.2.1.2.2.1.7.{index}`, `.8.{index}` | 3 / 2 |
| Interface speed, bits/s | `.1.3.6.1.2.1.2.2.1.5.{index}` | 12 |
| Incoming / outgoing octets, Counter32 | `.1.3.6.1.2.1.2.2.1.10.{index}`, `.16.{index}` | 4 / 5 |
| Incoming / outgoing errors | `.1.3.6.1.2.1.2.2.1.14.{index}`, `.20.{index}` | 8 / 9 |
| Incoming / outgoing discards | `.1.3.6.1.2.1.2.2.1.13.{index}`, `.19.{index}` | 10 / 11 |
| Per-logical-processor CPU load | `.1.3.6.1.2.1.25.3.3.1.2.{index}` | 14 |
| All processor loads (WALK root) | `.1.3.6.1.2.1.25.3.3.1.2` | 15 |
| Total physical memory, KiB | `.1.3.6.1.2.1.25.2.2.0` | 16 |
| CPU idle percentage | `.1.3.6.1.4.1.2021.11.11.0` | 17 |
| One-minute system load average | `.1.3.6.1.4.1.2021.10.1.3.1` | 18 |
| Available physical memory, KiB | `.1.3.6.1.4.1.2021.4.6.0` | 19 |
| Physical memory storage table | `.1.3.6.1.2.1.25.2.3` | 25 |
| Total received packets, Counter32 | `.1.3.6.1.4.1.32473.1.2.1.1.{index}` | Custom OID |
| Total transmitted packets, Counter32 | `.1.3.6.1.4.1.32473.1.2.1.2.{index}` | Custom OID |

Additional objects include `ifNumber`, `ifIndex`, `ifMtu`, `ifLastChange`,
`ifName`, `ifHighSpeed`, `hrSystemUptime`, and 64-bit incoming/outgoing octets
at `.1.3.6.1.2.1.31.1.1.1.6.{index}` and `.10.{index}`. Counter64 objects require
SNMPv2c; SNMPv1 walks skip them. Both standard Counter32 and high-capacity
Counter64 octet OIDs are converted to Mbps by the manager, with integer counter
precision preserved when parsing responses and CSV files.

Collection control objects use `.1.3.6.1.4.1.32473.1.3.1.0` for a JSON sampling
configuration. The collection table at `.1.3.6.1.4.1.32473.1.3.2` has columns
1=group name, 2=effective interval in seconds, 3=last collection time in seconds
since agent startup, and 4=collection count. Group indexes follow the listed
group order: system=1, interfaces=2, incoming=3, outgoing=4, CPU=5, RAM=6, load=7.
The CLI manages these objects; a read community cannot SET them.

### Measurement details

- CPU load and idle use a time-weighted rolling 60-second average. During
  startup they use the history available so far. The manager's average-CPU
  option averages the per-processor entries. Initial CPU samples need a sampling
  interval to accumulate meaningful history.
- RAM “used” means total minus OS-available memory, so reclaimable cache is
  included in available memory. The storage row is `Physical Memory`, index 1,
  type `hrStorageRam`. The manager multiplies storage units by their allocation
  size; units are based on page size and enlarged if needed for large hosts.
- `sysUpTime` measures this agent's uptime. `hrSystemUptime` measures host uptime.
- Traffic/error/discard counters come from the OS and wrap at 32 bits on the
  standard interface OIDs. A speed of zero means the OS did not report a speed.
  Administrative state uses interface flags where exposed by the platform,
  otherwise it follows the available up/down status.
- psutil exposes **total packets**, not a portable unicast-only breakdown.
  Consequently catalog 6/7 (`ifInUcastPkts` / `ifOutUcastPkts`) are not exposed.
  Select the custom packet OIDs above to capture packet rates; the manager
  reports their rates as units/s, where one unit is one packet.
- The UCD load-average objects use the platform's load-average support. psutil's
  Windows emulation needs about five seconds to initialize. If a platform has
  no load-average support, these objects are absent.
- Custom packet OIDs and `sysObjectID` use the documentation/example enterprise
  number 32473 for this lab agent.

## Optional SNMP SET

Set `write_community` to a string different from `read_community` to enable SET.
The writable objects are:

- `sysContact`: `.1.3.6.1.2.1.1.4.0`
- `sysName`: `.1.3.6.1.2.1.1.5.0`
- `sysLocation`: `.1.3.6.1.2.1.1.6.0`

Use Net-SNMP type `s` in the manager's SET menu. For example, if you configured
the write community as `lab-write`:

```bash
snmpset -v2c -c lab-write udp:192.168.1.20:1161 .1.3.6.1.2.1.1.6.0 s "Lab room 2"
```

These values are the **agent's reported metadata**; setting `sysName` does not
rename the operating system. Metrics and interface states are read-only. Values
must be UTF-8 strings of at most 255 bytes. A multi-object SET validates all
values before updating any of them.

`state_file` optionally persists these three values across restarts. Relative
paths are resolved beside the configuration file; a CLI `--state-file` is
resolved from the current directory. Persisted values take precedence over
configuration defaults. With `state_file: null`, SET values last until restart.
The Linux service supplies a writable state file automatically.

Ordinary SNMPv1/v2c sends communities without encryption. Enabling Hookah's
optional RSA/AES-GCM transport encrypts the complete packet, including community
strings, requests, and responses; see `ENCRYPTION.md`.

## Install as a Linux service on UDP 161

From the directory containing these files on the monitored device:

```bash
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin snmp-agent
sudo install -d /opt/snmp-python-agent
sudo install -m 755 snmp_agent.py /opt/snmp-python-agent/
sudo install -m 644 hookah_version.py snmp_protocol.py snmp_mib.py snmp_cli_help.py snmp_crypto.py requirements-agent.txt /opt/snmp-python-agent/
sudo python3 -m venv /opt/snmp-python-agent/.venv
sudo /opt/snmp-python-agent/.venv/bin/python -m pip install -r /opt/snmp-python-agent/requirements-agent.txt
sudo install -d -o snmp-agent -g snmp-agent -m 750 /var/lib/snmp-python-agent
sudo install -o snmp-agent -g snmp-agent -m 640 snmp_agent_config.example.json /var/lib/snmp-python-agent/config.json
sudo /opt/snmp-python-agent/.venv/bin/python /opt/snmp-python-agent/snmp_agent.py config set --config /var/lib/snmp-python-agent/config.json --set allowed_managers '["127.0.0.1/32", "192.168.1.10/32"]' --set write_community lab-write
sudo install -m 644 snmp-agent.service /etc/systemd/system/snmp-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now snmp-agent.service
```

Use the real manager IP/CIDR and desired communities in the CLI command above,
and leave `port` at `161` for standard SNMP. Allow that manager's UDP 161 access
through the device firewall if needed. The service grants the dedicated user
permission to bind a low-numbered port and provides its state directory.

Check service status and logs:

```bash
systemctl status snmp-agent.service
journalctl -u snmp-agent.service -f
```

Windows installation and native service commands are in `WINDOWS-AGENT.md`.

## Verification

The shared Agent/Manager testing environment is documented in `TESTING.md`.
Use the full source project (or `source/` in the combined release ZIP):

```bash
make setup-test
make test
```

Tests use a loopback-only agent on a dynamically selected unprivileged UDP port.
They exercise real GET/WALK/GETBULK/SET requests, CPU/RAM and traffic capture,
both executable CLIs, local/remote interval edits, live reload, persistence, and
synchronized polling. Unit tests verify 1/10/120-second schedules with virtual
clocks and test manager parsing, rate calculations, CSV compatibility, and
headless plots.
