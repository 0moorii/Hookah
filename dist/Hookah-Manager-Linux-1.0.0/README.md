# Hookah Manager 1.0.0 — Linux

Use **Hookah-Manager.sh** to query an Agent, capture CSV metrics, and create plots.
Edit **config.json** beside the launcher for your device and connection defaults.

## Files you use

```text
Hookah-Manager.sh
README.md
HELP.md
config.json
.hookah/          application, environment, dependencies, docs, and metadata
```

The support folder is hidden on Linux and marked hidden by the Windows launcher.
Keep the extracted directory together when moving it to another machine.

## Run immediately — dependencies included

The package includes portable **Python 3.12**, plotting/encryption libraries,
and a bundled **PySNMP** client backend. No Python, pip, Net-SNMP installation,
or internet connection is required. Run:

```text
./Hookah-Manager.sh doctor
./Hookah-Manager.sh --version
```

Installed dependencies are under `.hookah/environment`: PySNMP, cryptography,
Matplotlib, NumPy, Pillow, and their supporting modules. `setup` is an optional
offline verification command. To use existing external Net-SNMP clients instead,
set `snmp_bin` in `config.json` or pass `--snmp-bin PATH`.

The Windows `.exe` and Linux `.sh` automatically select the bundled runtime.
`HOOKAH_PYTHON` is an explicit developer override. On Linux, if extraction
discarded executable permissions, run `chmod +x Hookah-Manager.sh` first.

## Run

```text
./Hookah-Manager.sh
./Hookah-Manager.sh get --oid .1.3.6.1.2.1.1.1.0
./Hookah-Manager.sh walk --oid .1.3.6.1.2.1.2.2.1.2
./Hookah-Manager.sh capture --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
./Hookah-Manager.sh plot cpu.csv --output plots/cpu.png --no-show
./Hookah-Manager.sh config show
./Hookah-Manager.sh help capture
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

Read `HELP.md` or run `./Hookah-Manager.sh --help`. Detailed guides are grouped in
`.hookah/docs/`: `README-manager.md`, `WINDOWS-MANAGER.md`, `MIB.md`, and
`ENCRYPTION.md`. Implementation files stay in `.hookah/app/`.
