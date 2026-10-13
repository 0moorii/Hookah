# Hookah Manager @VERSION@ — Linux

Enter the release's `Manager/` folder. The Linux Manager command is **hookah-mg**.
All operations use parameters.
Running it without parameters prints help, never a numbered menu.

```text
hookah-mg
README.md
HELP.md
config.json
.hookah/          portable Python, application, dependencies, saved agents, and guides
```

Keep the complete directory together. Python 3.12, the PySNMP client, plotting,
and encryption dependencies are included. No installation or network download
is needed. If necessary, restore permissions with `chmod +x hookah-mg`.

From this folder:

```bash
./hookah-mg -v
./hookah-mg --doctor
./hookah-mg --install-cli
export PATH="$HOME/.local/bin:$PATH"
hookah-mg -a 192.168.1.20
hookah-mg -a 192.168.1.21
hookah-mg --agents
hookah-mg --select-agent 192.168.1.21
hookah-mg --get --oid .1.3.6.1.2.1.1.1.0
```

The first saved Agent becomes active. Later additions preserve the active
selection. `--remove-agent ADDRESS` removes an entry. Addresses default to UDP
1161; specify `IP:port` or `--port PORT` for other ports. Adding or listing Agents
does not scan the network. Saved lists stay hidden in `.hookah/config/`.

```bash
hookah-mg --capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
hookah-mg --plot cpu.csv --output plots/cpu.png --no-show
hookah-mg --config-show
```

The visible `config.json` stores the active target and connection defaults.
`--target ADDRESS` overrides the target for one command. `--config PATH` selects
another profile and saved Agent list. Relative profile paths resolve beside the
profile; command parameters and output paths work from any current directory.

`--setup` verifies the included runtime offline. `--snmp-bin PATH` optionally
selects external Net-SNMP clients; `HOOKAH_PYTHON` selects a developer interpreter.
Run `hookah-mg --help` or read `HELP.md`. The full operation, output, plotting,
encryption, and saved-Agent reference is `.hookah/docs/LINUX-CLI.md`.
Windows commands and menus remain unchanged.
