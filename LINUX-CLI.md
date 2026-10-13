# Linux parameter commands

The Linux Agent uses **hookah-ag**. The Linux Manager uses **hookah-mg**.
Every operation uses command-line parameters. Neither Linux entrypoint opens
the numbered menu or prompts for missing values. Windows entrypoints and menus
are unchanged.

## Make the commands available on PATH

From each extracted Linux package, run the corresponding command:

```bash
./hookah-ag --install-cli
./hookah-mg --install-cli
export PATH="$HOME/.local/bin:$PATH"
```

Run the Agent installation command on monitored devices and the Manager command
on the monitoring machine. Both commands can coexist on one machine.
`--bin-dir PATH` selects another installation directory. Installation creates a
symlink to the package; keep the complete extracted package in its current
location. An existing unrelated command is never overwritten. Without PATH
installation, use `./hookah-ag` or `./hookah-mg` inside the appropriate package.

The private `.hookah/` folder contains portable Python, dependencies, application
files, and saved agent lists. No Python, pip, or Net-SNMP installation is needed.
The visible `config.json` remains the editable profile. `--config PATH` selects
another profile, with its own saved agent list.
`HOOKAH_MANAGER_CONFIG` can select the Manager profile; `--config` takes priority.

## Agent

```bash
hookah-ag -v
hookah-ag --help
hookah-ag --setup
hookah-ag --config-show
hookah-ag -a 192.168.1.10
hookah-ag --start
```

With no parameters, `hookah-ag` starts the foreground Agent. `--start` makes that
action explicit. The package initially listens on UDP 1161 and permits loopback
only. `-a IP_OR_CIDR` adds an allowed **Manager**, preserves the current allowlist,
and saves the result. Restart the Agent after changing bind addresses,
communities, or permitted Managers. Press Ctrl+C to stop the foreground process.

Startup overrides: `--listen-address`, `--port`, repeatable `--manager`,
`--sample-interval`, `--state-file`, and `--log-level`.

Configuration and local metrics:

```bash
hookah-ag --config-init --config profiles/new-agent.json
hookah-ag --config-set --sample-interval 1 --interval ifInOctets 10
hookah-ag --config-set --clear-interval ifInOctets
hookah-ag --config-set --set read_community public --set write_community lab-write
hookah-ag --config-set --encryption on --rsa-private-key /path/agent_private.pem
hookah-ag --list-oids
hookah-ag --mib --group memory
hookah-ag --mib --group memory --values
```

`--set FIELD VALUE` can repeat; JSON arrays and objects are accepted as values.
`--rsa-peer-public-key PATH` saves an explicit trusted peer. All intervals are
finite positive seconds. Sampling changes reload without restarting.

Remote Agent configuration:

```bash
hookah-ag --config-show --remote udp:192.168.1.20:1161 --community public
hookah-ag --config-set --remote udp:192.168.1.20:1161 --community lab-write --interval cpu 10
```

Remote options include `--snmp-version {1,2c}`, `--timeout SECONDS`, and
`--encryption-config PATH`. Remote writes require the Agent's enabled write
community and a permitted source address. `-v` always prints the application
version, never the SNMP version.

## Manager and multiple agents

```bash
hookah-mg
hookah-mg -v
hookah-mg -a 192.168.1.20
hookah-mg -a 192.168.1.21 --port 1161
hookah-mg --agents
hookah-mg --select-agent 192.168.1.21
hookah-mg --remove-agent 192.168.1.20
```

With no parameters, the Manager prints parameter help, including on a terminal.
`-a ADDRESS` saves an Agent IP or hostname, optionally with `:port` or a `udp:`
prefix. The default port is 1161. Repeat `-a` to add several agents in one command.
The first saved Agent becomes active; subsequent additions preserve the active
selection. Duplicate addresses are not added twice. `--agents` marks the active
address with `*`; selection never requires a number.

`--select-agent ADDRESS` saves the selected target in `config.json`. Removing
the active Agent selects the first remaining saved address, or clears the target
when none remain. Saved lists live in `.hookah/config/`. Listing and adding
addresses do not scan the network or contact the devices.

All former menu operations are parameter commands:

```bash
hookah-mg --get --oid .1.3.6.1.2.1.1.1.0
hookah-mg --walk --oid .1.3.6.1.2.1.2.2.1.2 --output interfaces.csv
hookah-mg --set --community lab-write --oid .1.3.6.1.2.1.1.6.0 --type s --value "Lab room 2"
hookah-mg --capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --sync-agent --output cpu.csv
hookah-mg --plot cpu.csv --output plots/cpu.png --color red --no-show
hookah-mg --catalog
hookah-mg --compare before.csv after.csv --output comparison.png
hookah-mg --config-set --encryption-config keys/manager_encryption.json
hookah-mg --config-set --disable-encryption
hookah-mg --generate-keys --directory keys --bits 3072
hookah-mg --mib --group storage
hookah-mg --mib --group storage --values
hookah-mg --browse --group memory
hookah-mg --help
```

SNMP SET writes to the remote device; it requires the write community and
appropriate Agent permissions. It does not display an interactive confirmation.

Connection parameters: `--target`, `--port`, `--community`, `--snmp-version`,
`--timeout`, `--retries`, `--encryption-config`, and `--snmp-bin`. `--target`
overrides the active Agent for one command. `--no-config` ignores saved defaults
and requires an explicit target for network operations.

Output modes: omit `--output` for terminal-only GET/WALK/SET; add `--output PATH`
for terminal and CSV; add `--quiet` with `--output PATH` for CSV-only output.
Errors still appear on stderr. Capture always requires `--output` and also accepts
`--interval SECONDS`, `--duration SECONDS`, `--counter-bits {auto,32,64}`, and
`--sync-agent`. Without a duration, capture runs until Ctrl+C.

Plotting parameters include `--show`/`--no-show`, `--scale {auto,linear,symlog}`,
`--memory-mode {used,percent,both}`, and `--color`. Comparison accepts memory
modes `used` or `percent`, `--label-first`, `--label-second`, and `--title`.
`--root OID` filters MIB/browse queries. `--mib` stays offline unless `--values`,
`--target`, or CSV output requests live values. `--banner` explicitly prints the
startup banner; normal commands remain scriptable.

Profile and dependency commands:

```bash
hookah-mg --config-show
hookah-mg --config-init --config profiles/new-manager.json
hookah-mg --config-set --community public --snmp-version 2c --timeout 2 --retries 1
hookah-mg --doctor
hookah-mg --setup
```

`--config-set` also saves `--target`, `--interval`, `--counter-bits`, `--snmp-bin`,
or `--encryption-config`. Paths supplied as parameters resolve from the current
working directory; relative paths already in the profile remain relative to it.
Missing required values and unsupported parameter combinations produce usage
errors rather than prompts. Manager exit codes remain 0 for success, 1 for a
runtime error, 2 for a usage error, and 130 for Ctrl+C.
