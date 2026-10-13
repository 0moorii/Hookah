# Hookah Agent Linux parameter help

Public command: **hookah-ag**, version @VERSION@. No numbered menus or prompts.

```text
hookah-ag -v                                      Print the application version
hookah-ag --help                                  Describe every parameter
hookah-ag --install-cli                           Install a symlink in ~/.local/bin
hookah-ag --setup                                 Verify bundled dependencies offline
hookah-ag --start                                 Start the foreground Agent
hookah-ag -a 192.168.1.10                          Add a permitted Manager
hookah-ag --config-show                           Show the visible profile
hookah-ag --config-init --config profiles/new.json Create a new profile
hookah-ag --config-set --sample-interval 10        Save the default sampling interval
hookah-ag --config-set --interval ifInOctets 120   Save a per-metric interval
hookah-ag --config-set --clear-interval ifInOctets Remove a metric override
hookah-ag --config-set --set write_community lab-write
hookah-ag --list-oids                             Inspect local metrics without listening
hookah-ag --mib --group memory --values            Show named local metrics
hookah-ag --config-show --remote udp:192.168.1.20:1161 --community public
hookah-ag --config-set --remote udp:192.168.1.20:1161 --community lab-write --interval cpu 10
```

Use `./hookah-ag` before PATH installation. `--bin-dir PATH` selects another
installation directory. The package initially listens on UDP 1161 and permits
loopback only. Bare `hookah-ag` starts the foreground Agent; Ctrl+C stops it.

Startup parameters: `--config`, `--listen-address`, `--port`, repeatable
`--manager`, `--sample-interval`, `--state-file`, and `--log-level`.
Configuration parameters: repeatable `--set FIELD VALUE`, `--interval METRIC
SECONDS`, `--clear-interval METRIC`, `--encryption {on,off}`, `--rsa-private-key`,
and `--rsa-peer-public-key`. Intervals are finite positive seconds.
Remote parameters: `--remote`, `--community`, `--snmp-version {1,2c}`,
`--timeout`, and `--encryption-config`. Remote writes need the write community.
`-v` always prints the application version.

Full examples and behavior are in `.hookah/docs/LINUX-CLI.md`. This Linux
parameter interface does not change Windows commands or menus.
