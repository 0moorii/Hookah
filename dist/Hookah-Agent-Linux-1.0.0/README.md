# Hookah Agent 1.0.0 — Linux

The Agent runs on the device you want to monitor. Use **Hookah-Agent.sh** from your
terminal; its editable settings are in **config.json** beside it.

## Files you use

```text
Hookah-Agent.sh
README.md
HELP.md
config.json
.hookah/          application, environment, dependencies, docs, and service files
```

The support folder is hidden on Linux and marked hidden by the Windows launcher.
Keep the extracted directory together when moving it to another machine.

## Run immediately — dependencies included

The package includes portable **Python 3.12** and the installed Agent dependencies.
No Python installation, pip command, or internet connection is needed. Run:

```text
./Hookah-Agent.sh --version
./Hookah-Agent.sh config show
./Hookah-Agent.sh
```

Both launchers automatically use `.hookah/environment`, which contains Python,
PySNMP, psutil, cryptography, and their dependencies. The Windows bundle also
includes pywin32 for service support. `setup` is an optional offline verification
command; it does not download or install anything in these packages.
`HOOKAH_PYTHON` can explicitly select a developer interpreter instead.

On Linux, if your ZIP extractor discarded executable permissions, run
`chmod +x Hookah-Agent.sh` first. Commands and output paths work from other directories,
including paths containing spaces. `config.json` is always located beside the
public launcher; `--config PATH` selects a different profile.

## Defaults and LAN use

The Agent listens on UDP **1161** with read community **public** and allows
loopback requests initially. Edit `allowed_managers` in `config.json` to include
your Manager's IP/CIDR before monitoring this host over the LAN. Numeric
collection intervals are in seconds. For example:

```text
./Hookah-Agent.sh config set --interval ifInOctets 10
./Hookah-Agent.sh config set --sample-interval 1
./Hookah-Agent.sh config show
```

Sampling settings reload live. Listening addresses, communities, and permitted
Manager addresses take effect after restarting the Agent. Press Ctrl+C to stop
the foreground process. The Manager receives responses to its own requests.

## Help and services

Read `HELP.md`, run `./Hookah-Agent.sh --help`, or use `./Hookah-Agent.sh help config set` for
command-specific options. Windows users can install the native auto-start
service from administrator PowerShell with `./Hookah-Agent.sh service install` and
remove it with `./Hookah-Agent.sh service uninstall`.

Detailed guides are grouped in `.hookah/docs/`: `README-agent.md`,
`WINDOWS-AGENT.md`, `MIB.md`, and `ENCRYPTION.md`. Service files are in
`.hookah/platform/`. Python implementation files stay in `.hookah/app/`.
