# Hookah Agent command help

Public entrypoint: **@LAUNCHER@** (@PLATFORM@, version @VERSION@).

```text
@COMMAND@ setup                       Install private Python dependencies
@COMMAND@ --version                   Print the application version
@COMMAND@ --help                      Print this guide
@COMMAND@                            Start the foreground Agent
@COMMAND@ config show                 Show the adjacent config.json
@COMMAND@ config set --sample-interval 10
@COMMAND@ config set --interval ifInOctets 120
@COMMAND@ config set --clear-interval ifInOctets
@COMMAND@ --config profiles/lab.json   Start with another configuration
@COMMAND@ --port 1161 --manager 192.168.1.10
@COMMAND@ --list-oids                  Inspect local metrics without listening
@COMMAND@ mib --group memory --values
@COMMAND@ help config set             Show every configuration option
```

Intervals are finite positive numbers in seconds. Configuration updates validate
before replacing the file, and sampling changes apply to a running Agent.
The default profile listens on UDP 1161 and permits loopback only.

Remote configuration uses the same interface:

```text
@COMMAND@ config show --remote udp:192.168.1.20:1161 --community public
@COMMAND@ config set --remote udp:192.168.1.20:1161 --community lab-write --interval cpu 10
```

Remote writes require the enabled write community and an allowed source IP.
Optional `--encryption-config PATH` selects an encrypted remote transport profile.

Windows service installation (administrator PowerShell):

```text
@COMMAND@ service install -ManagerAddresses 192.168.1.10/32 -Port 1161
@COMMAND@ service uninstall
```

Python 3.10+ is required for runtime and setup. All setup and implementation files
are kept in `.hookah/`; the public commands manage the adjacent `config.json`.
Detailed MIB, encryption, Linux/systemd, and Windows service guides are under
`.hookah/docs/` and `.hookah/platform/`.
