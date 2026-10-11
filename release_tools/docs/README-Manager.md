# Hookah Manager @VERSION@ — @PLATFORM@

Use **@LAUNCHER@** to query an Agent, capture CSV metrics, and create plots.
Edit **config.json** beside the launcher for your device and connection defaults.

## Files you use

```text
@LAUNCHER@
README.md
HELP.md
config.json
.hookah/          application, environment, dependencies, docs, and metadata
```

The support folder is hidden on Linux and marked hidden by the Windows launcher.
Keep the extracted directory together when moving it to another machine.

## First-time setup

Install **Python 3.10+** and the **Net-SNMP command-line clients**, then run:

```text
@COMMAND@ setup
@COMMAND@ doctor
@COMMAND@ --version
```

Setup installs the private environment into `.hookah/environment`. On Windows,
Net-SNMP can be selected with `@COMMAND@ setup --snmp-bin "C:/Program Files/Net-SNMP/bin"`.
On Debian/Ubuntu install clients with `sudo apt install snmp python3-venv`;
Fedora/RHEL uses `sudo dnf install net-snmp-utils`, Arch uses
`sudo pacman -S net-snmp`, and openSUSE uses `sudo zypper install net-snmp`.

The Windows `.exe` is a native x64 console launcher. It prefers `HOOKAH_PYTHON`,
the private environment, `py -3`, then `python`. The Linux `.sh` prefers
`HOOKAH_PYTHON`, the private environment, then `python3`. On Linux, if extraction
discarded executable permissions, run `chmod +x @LAUNCHER@` first.

## Run

```text
@COMMAND@
@COMMAND@ get --oid .1.3.6.1.2.1.1.1.0
@COMMAND@ walk --oid .1.3.6.1.2.1.2.2.1.2
@COMMAND@ capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
@COMMAND@ plot cpu.csv --output plots/cpu.png --no-show
@COMMAND@ config show
@COMMAND@ help capture
```

The initial profile targets a local Agent on UDP 1161 with community `public`.
Set `target` and `community` in `config.json` for LAN devices. CLI flags override
profile values. Relative encryption/tool paths in the profile resolve beside it.
The launcher uses the adjacent profile even from a different working directory;
`--config PATH` or `HOOKAH_MANAGER_CONFIG` selects another profile.

Scriptable commands print results without a banner. With no command, a terminal
opens the menu; redirected input prints help. Plot and comparison commands save
PNGs without prompts or a graphical display unless you request `--show`.

## Help

Read `HELP.md` or run `@COMMAND@ --help`. Detailed guides are grouped in
`.hookah/docs/`: `README-manager.md`, `WINDOWS-MANAGER.md`, `MIB.md`, and
`ENCRYPTION.md`. Implementation files stay in `.hookah/app/`.
