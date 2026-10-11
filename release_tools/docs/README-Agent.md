# Hookah Agent @VERSION@ — @PLATFORM@

The Agent runs on the device you want to monitor. Use **@LAUNCHER@** from your
terminal; its editable settings are in **config.json** beside it.

## Files you use

```text
@LAUNCHER@
README.md
HELP.md
config.json
.hookah/          application, environment, dependencies, docs, and service files
```

The support folder is hidden on Linux and marked hidden by the Windows launcher.
Keep the extracted directory together when moving it to another machine.

## First-time setup

Install **Python 3.10+**, then run:

```text
@COMMAND@ setup
@COMMAND@ --version
@COMMAND@ config show
@COMMAND@
```

Setup installs the dependencies into `.hookah/environment`. The Windows `.exe`
is a native x64 console launcher. It locates Python through `HOOKAH_PYTHON`, its
private environment, the Windows `py` launcher, or `python`. The Linux `.sh`
prefers `HOOKAH_PYTHON`, its private environment, then `python3`.

On Linux, if your ZIP extractor discarded executable permissions, run
`chmod +x @LAUNCHER@` first. Commands and output paths work from other directories,
including paths containing spaces. `config.json` is always located beside the
public launcher; `--config PATH` selects a different profile.

## Defaults and LAN use

The Agent listens on UDP **1161** with read community **public** and allows
loopback requests initially. Edit `allowed_managers` in `config.json` to include
your Manager's IP/CIDR before monitoring this host over the LAN. Numeric
collection intervals are in seconds. For example:

```text
@COMMAND@ config set --interval ifInOctets 10
@COMMAND@ config set --sample-interval 1
@COMMAND@ config show
```

Sampling settings reload live. Listening addresses, communities, and permitted
Manager addresses take effect after restarting the Agent. Press Ctrl+C to stop
the foreground process. The Manager receives responses to its own requests.

## Help and services

Read `HELP.md`, run `@COMMAND@ --help`, or use `@COMMAND@ help config set` for
command-specific options. Windows users can install the native auto-start
service from administrator PowerShell with `@COMMAND@ service install` and
remove it with `@COMMAND@ service uninstall`.

Detailed guides are grouped in `.hookah/docs/`: `README-agent.md`,
`WINDOWS-AGENT.md`, `MIB.md`, and `ENCRYPTION.md`. Service files are in
`.hookah/platform/`. Python implementation files stay in `.hookah/app/`.
