# Hookah Manager command help

Public entrypoint: **@LAUNCHER@** (@PLATFORM@, version @VERSION@).

```text
@COMMAND@ setup                       Verify the bundled offline runtime
@COMMAND@ setup --snmp-bin PATH       Set the Net-SNMP executable directory
@COMMAND@ doctor                      Check dependencies and tool paths
@COMMAND@ --version                   Print the application version
@COMMAND@ --help                      Print this guide
@COMMAND@                            Open the interactive terminal menu
@COMMAND@ help capture                Explain a specific command
@COMMAND@ config show                 Show resolved config.json settings
@COMMAND@ get --oid .1.3.6.1.2.1.1.1.0
@COMMAND@ walk --oid .1.3.6.1.2.1.2.2.1.2 --output interfaces.csv
@COMMAND@ browse --group memory
@COMMAND@ mib --group storage          Show an offline MIB tree
@COMMAND@ catalog                     List available OID choices
@COMMAND@ capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
@COMMAND@ plot cpu.csv --output plots/cpu.png --no-show
@COMMAND@ compare before.csv after.csv --output comparison.png
@COMMAND@ keys generate --directory keys
```

Connection options: `--target udp:host:port`, `--community VALUE`,
`--version {1,2c}`, `--timeout SECONDS`, `--retries {0..10}`,
`--encryption-config PATH`, `--snmp-bin PATH`, and `--config PATH`.
`--no-config` ignores the profile and requires a target for network operations.
At the top level, `--version` is the Hookah version; after a network command,
it selects the SNMP protocol version.

Capture options: `--interval SECONDS`, `--duration SECONDS`, `--sync-agent`,
`--counter-bits {auto,32,64}`, and required `--output PATH`.
Use WALK to discover the actual interface index before capturing counters.

Plot options: `--output PATH`, `--color COLOR`, `--scale {auto,linear,symlog}`,
`--memory-mode {used,percent,both}`, `--no-show`, and `--show`.
Comparison accepts `--label-first`, `--label-second`, `--title`, and memory
modes `used` or `percent`. Output directories are created automatically.

Authorized SNMP SET:

```text
@COMMAND@ set --community lab-write --oid .1.3.6.1.2.1.1.6.0 --type s --value "Lab room 2"
```

Exit codes: 0=success, 1=runtime error, 2=usage error, 130=Ctrl+C.
Python, PySNMP clients, encryption, and plotting dependencies are included.
No dependency installation is needed. `--snmp-bin` optionally selects external
Net-SNMP clients. Every implementation/support file is under `.hookah/`.
