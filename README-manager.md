# Hookah Manager 1.0.0-2

## Packaged CLI entrypoints

End-user ZIPs are `Hookah-Manager-Linux-1.0.0-2.zip` and
`Hookah-Manager-Windows-1.0.0-2.zip`. They expose only `Hookah-Manager.sh` or
`Hookah-Manager.exe`, `README.md`, `HELP.md`, and `config.json`; supporting files
are grouped under `.hookah/`. Run the public launcher directly, then use
the same launcher for all commands. Python, plotting/encryption libraries, and
a PySNMP client backend are included; `setup` only verifies the offline bundle.
The source-project `hookah-manager` wrapper
used in examples below is the developer equivalent.

The Manager runs on the monitoring machine. It uses Net-SNMP clients to query
the Agent or another SNMPv1/v2c device, saves CSVs, calculates counter rates,
and plots single or compared sessions. Run `./hookah-manager help` for commands
or `./hookah-manager help capture` for a specific topic.

## Linux installation

Requirements: **Python 3.10+**, a virtual-environment-capable Python installation,
and `snmpget`, `snmpwalk`, and `snmpset` when using the developer source wrapper.
The packaged CLI includes its own Python/runtime/client dependencies and needs
no separate installation on x86-64 glibc Linux (glibc 2.28+).

| Distribution | Net-SNMP installation |
| --- | --- |
| Debian / Ubuntu | `sudo apt install python3-venv snmp` |
| Fedora / RHEL | `sudo dnf install python3 net-snmp-utils` |
| Arch Linux | `sudo pacman -S python net-snmp` |
| openSUSE | `sudo zypper install python3 net-snmp` |

Extract `Hookah-Manager-Linux-1.0.0-2.zip`, enter the extracted directory, then:

```bash
chmod +x Hookah-Manager.sh
./Hookah-Manager.sh setup
./Hookah-Manager.sh doctor
./Hookah-Manager.sh --version
```

The launcher locates files relative to itself, so it works from another working
directory. It prefers `.hookah/environment`; `HOOKAH_PYTHON=/path/to/python`
selects another interpreter. From the developer project, the source wrapper
prefers `.venv`, and Python entrypoints are also available directly. Requirements include
Matplotlib and cryptography; only the source wrapper needs Net-SNMP installed
through the OS separately. Packaged commands use the included PySNMP backend.

Windows native `.exe` setup and launch commands are in `WINDOWS-MANAGER.md`.

## CLI defaults and configuration

The packaged `config.json` (or source-project `snmp_manager_config.json`) targets a local Agent on UDP 1161:

```json
{
  "target": "udp:127.0.0.1:1161",
  "version": "2c",
  "community": "public",
  "interval": 1.0,
  "timeout": 2.0,
  "retries": 1,
  "counter_bits": "auto",
  "snmp_bin": null,
  "encryption_config": null
}
```

In an end-user package the launcher selects its adjacent `config.json`, with
`--config PATH` and `HOOKAH_MANAGER_CONFIG` available as explicit overrides.
For direct source-project execution, configuration selection is: explicit `--config PATH`, then
`HOOKAH_MANAGER_CONFIG`, then `snmp_manager_config.json` in the working directory,
then the file beside the Manager. `--no-config` uses built-in defaults and
requires `--target` for network commands. Command flags override profile fields.
Relative `snmp_bin` and `encryption_config` paths resolve beside the JSON file;
CLI paths resolve from the current directory. Unknown fields and invalid values
are rejected. Intervals/timeouts are finite, positive numeric seconds; retries
are an integer from 0 to 10. `counter_bits` is `"auto"`, `"32"`, or `"64"`.

```bash
./hookah-manager config show
./hookah-manager config init --config profiles/lab.json
./hookah-manager --config profiles/lab.json get --oid .1.3.6.1.2.1.1.1.0
```

`config init` creates a profile without overwriting an existing file. Change the
profile's target/community for your LAN. `snmp_manager_config.example.json` is
the reference template. For an offline MIB tree, `mib` queries a device only
when `--target` is explicitly supplied, even if a profile has a target.

## Query, change, and capture

```bash
# Read OS information, then discover interface indexes.
./hookah-manager get --oid .1.3.6.1.2.1.1.1.0
./hookah-manager walk --oid .1.3.6.1.2.1.2.2.1.2 --output interfaces.csv

# Aggregate CPU and physical-memory summary, matching the interactive menu.
./hookah-manager get --oid .1.3.6.1.2.1.25.3.3.1.2
./hookah-manager get --oid .1.3.6.1.2.1.25.2.3

# Select a real interface index from WALK; this example uses index 1.
./hookah-manager capture --oid .1.3.6.1.2.1.2.2.1.10.1 \
  --interval 1 --duration 30 --output incoming.csv
./hookah-manager capture --oid .1.3.6.1.2.1.25.3.3.1.2 \
  --sync-agent --duration 10 --output cpu.csv

# Requires the Agent's enabled write community.
./hookah-manager set --community lab-write --oid .1.3.6.1.2.1.1.6.0 \
  --type s --value "Lab room 2"
```

Network commands accept `--target`, `--version {1,2c}`, `--community`,
`--timeout`, `--retries`, `--snmp-bin`, and `--encryption-config`. At the top
level, `--version` prints the Hookah application version; after a network
command it selects the SNMP protocol version. GET/WALK/SET can append to a CSV
with `--output`. Only tools needed by the selected operation are required.

Counter capture preserves Counter64 integer precision and calculates rates
from actual counter-change intervals. `--sync-agent` follows the Agent's live
collection schedule. CPU/RAM captures use the shared aggregate helpers. Duration
is bounded, even with long collection intervals; a capture with no successful
samples returns an error. The Agent configuration CLI handles remote collection
changes; see `README-agent.md` in the Agent ZIP or full source release.

## Headless plotting on Linux / SSH

CLI plotting saves PNGs without prompts or a display server. GUI windows are
opened only with `--show`. The original menu retains interactive graph choices.

```bash
./hookah-manager plot cpu.csv --output plots/cpu.png --no-show --color green
./hookah-manager plot incoming.csv --output plots/incoming.png --scale symlog
./hookah-manager plot memory.csv --memory-mode percent --output plots/ram.png
./hookah-manager compare cpu-before.csv cpu-after.csv --output plots/cpu-comparison.png \
  --label-first Before --label-second After --title "CPU comparison"
```

The destination directory is created automatically. Memory plots support
`used`, `percent`, or `both`; comparisons support `used` or `percent`.
Axis scaling accepts `auto`, `linear`, or `symlog` for numeric rates/gauges.
Missing, empty, incompatible, or non-numeric inputs report an error exit status.

## Terminal and automation behavior

- No arguments on a terminal opens the menu and banner. With redirected input,
  no arguments prints help and exits, avoiding an unexpected prompt.
- `interactive` explicitly opens the menu, including with scripted input.
- Scriptable commands print results without the banner. `--banner` opts in;
  `--no-banner` suppresses it in the menu.
- Errors and dependency instructions go to stderr. Exit codes are **0** for
  success, **1** for runtime errors, **2** for usage errors, and **130** for Ctrl+C.
- Broken output pipes close quietly, and EOF exits the menu cleanly.
- `help`, `catalog`, offline `mib`, `keys generate`, `config`, and plotting can
  run without Net-SNMP clients. `doctor` checks prerequisites without network I/O.

The expanded device tree and available metric families are in `MIB.md`.
Optional encrypted transport and PEM generation are in `ENCRYPTION.md`.
Verification and release-build commands are in `TESTING.md`.
