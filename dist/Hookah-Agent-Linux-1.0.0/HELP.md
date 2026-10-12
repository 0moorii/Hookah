# Hookah Agent command help

Public entrypoint: **Hookah-Agent.sh** (Linux, version 1.0.0).

```text
./Hookah-Agent.sh setup                       Verify bundled dependencies offline
./Hookah-Agent.sh --version                   Print the application version
./Hookah-Agent.sh --help                      Print this guide
./Hookah-Agent.sh                            Start the foreground Agent
./Hookah-Agent.sh config show                 Show the adjacent config.json
./Hookah-Agent.sh config set --sample-interval 10
./Hookah-Agent.sh config set --interval ifInOctets 120
./Hookah-Agent.sh config set --clear-interval ifInOctets
./Hookah-Agent.sh --config profiles/lab.json   Start with another configuration
./Hookah-Agent.sh --port 1161 --manager 192.168.1.10
./Hookah-Agent.sh --list-oids                  Inspect local metrics without listening
./Hookah-Agent.sh mib --group memory --values
./Hookah-Agent.sh help config set             Show every configuration option
```

Intervals are finite positive numbers in seconds. Configuration updates validate
before replacing the file, and sampling changes apply to a running Agent.
The default profile listens on UDP 1161 and permits loopback only.

Remote configuration uses the same interface:

```text
./Hookah-Agent.sh config show --remote udp:192.168.1.20:1161 --community public
./Hookah-Agent.sh config set --remote udp:192.168.1.20:1161 --community lab-write --interval cpu 10
```

Remote writes require the enabled write community and an allowed source IP.
Optional `--encryption-config PATH` selects an encrypted remote transport profile.

Windows service installation (administrator PowerShell):

```text
./Hookah-Agent.sh service install -ManagerAddresses 192.168.1.10/32 -Port 1161
./Hookah-Agent.sh service uninstall
```

Python and the Agent dependencies are included. Run directly after extraction;
`setup` only verifies the offline bundle. Keep the whole `.hookah/` folder beside
the launcher. Public commands manage the adjacent `config.json`.
Detailed MIB, encryption, Linux/systemd, and Windows service guides are under
`.hookah/docs/` and `.hookah/platform/`.
