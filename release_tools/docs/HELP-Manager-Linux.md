# Hookah Manager Linux parameter help

Public command: **hookah-mg**, version @VERSION@. No numbered menus or prompts.

```text
hookah-mg                                        Print parameter help
hookah-mg -v                                     Print the application version
hookah-mg --help                                 Describe every parameter
hookah-mg --install-cli                          Install a symlink in ~/.local/bin
hookah-mg -a 192.168.1.20                         Add an Agent (default UDP 1161)
hookah-mg --agents                               List saved Agents and mark the active one
hookah-mg --select-agent 192.168.1.20             Select a saved Agent
hookah-mg --remove-agent 192.168.1.20             Remove a saved Agent
hookah-mg --get --oid .1.3.6.1.2.1.1.1.0
hookah-mg --walk --oid .1.3.6.1.2.1.2.2.1.2 --output interfaces.csv
hookah-mg --set --community lab-write --oid .1.3.6.1.2.1.1.6.0 --type s --value "Lab room 2"
hookah-mg --capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
hookah-mg --plot cpu.csv --output plots/cpu.png --no-show
hookah-mg --catalog
hookah-mg --compare before.csv after.csv --output comparison.png
hookah-mg --config-set --encryption-config keys/manager_encryption.json
hookah-mg --config-set --disable-encryption
hookah-mg --generate-keys --directory keys --bits 3072
hookah-mg --mib --group storage
hookah-mg --mib --group storage --values
hookah-mg --browse --group memory
hookah-mg --config-show
hookah-mg --config-init --config profiles/new.json
hookah-mg --config-set --community public --snmp-version 2c
hookah-mg --doctor
hookah-mg --setup
```

Use `./hookah-mg` before PATH installation. `--bin-dir PATH` selects another
installation directory. The first saved Agent becomes active; later additions
preserve the selection. Saved lists are in `.hookah/config/`, and the active
target is in visible `config.json`. `--config PATH` selects another profile/list.
`--target ADDRESS` overrides the active Agent for one command.

Connection parameters: `--target`, `--port`, `--community`, `--snmp-version
{1,2c}`, `--timeout`, `--retries`, `--encryption-config`, and `--snmp-bin`.
`-v` always prints the application version. `--no-config` ignores saved defaults.
SNMP SET writes to the remote device without a confirmation prompt and requires
the permitted write community.

Output: no `--output` means terminal only; `--output PATH` means terminal and
CSV; `--output PATH --quiet` means CSV only. Capture requires `--output` and
accepts `--interval`, `--duration`, `--counter-bits`, and `--sync-agent`.
Omit duration to capture until Ctrl+C.

Graphs: `--show`/`--no-show`, `--color`, `--scale {auto,linear,symlog}`,
`--memory-mode {used,percent,both}`. Comparison supports memory modes `used` or
`percent`, `--label-first`, `--label-second`, and `--title`.
MIB/browse filters: `--group`, `--root`. Use `--values` for a live MIB query.
`--banner` explicitly prints the startup banner. Missing arguments produce
usage errors, never prompts.

Exit codes: 0=success, 1=runtime error, 2=usage error, 130=Ctrl+C.
The full reference is `.hookah/docs/LINUX-CLI.md`. Windows is unchanged.
