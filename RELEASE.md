# Hookah release 1.0.0

## Clean, grouped distributions

The Agent and Manager have separate **Linux** and **Windows x64** packages.
Each exposes exactly one executable launcher, `README.md`, `HELP.md`, and
`config.json`. All implementation/support files are grouped in `.hookah/`:

- `app/`: Python application and shared protocol/MIB modules.
- `dependencies/`: runtime requirements.
- `config/`: templates and fresh defaults.
- `docs/`: detailed component, MIB, encryption, and testing guides.
- `platform/`: OS/service integration.
- `metadata/`: version, manifests, and checksums.
- `environment/`: the private environment created by `setup`.

Windows `.exe` entrypoints are native PE32+ x64 console programs, built from
the included C source with standard Windows DLL dependencies. Linux `.sh`
entrypoints preserve executable permissions. The support folder carries hidden
ZIP attributes and is marked hidden by the Windows executable.

## Artifacts

```text
Hookah-Agent-Linux-1.0.0.zip
Hookah-Agent-Windows-1.0.0.zip
Hookah-Manager-Linux-1.0.0.zip
Hookah-Manager-Windows-1.0.0.zip
Hookah-1.0.0.zip
SHA256SUMS
VALIDATION.json
```

The previous `Hookah-Agent-1.0.0.zip`, `Hookah-Manager-1.0.0.zip`, and
`Hookah-Agent-Windows.zip` names are refreshed Windows-package aliases.
The combined ZIP groups ready-to-run folders as `Agent/Linux`, `Agent/Windows`,
`Manager/Linux`, and `Manager/Windows`. Source/tests are under `.hookah/source/`
and release verification results under `.hookah/metadata/`.

## Runtime

Install Python **3.10+** and invoke the public launcher with `setup`. Dependencies
are installed into the private environment; the visible `config.json` is retained.
The Manager additionally needs Net-SNMP clients. Version and top-level help in
the Windows executable work before Python setup. Arguments, Unicode paths,
spaces, quotes, and child exit statuses are preserved.

Default profiles use UDP 1161 and matching `public` read access for a loopback
trial. The Agent initially permits loopback only. Set the real Manager addresses,
communities, and optional encryption profiles for LAN deployment.

## Verification

`make release` builds the Windows launchers, runs all unit and loopback integration
checks, then runs the short smoke test before producing release ZIPs. Native
launcher tests execute on Windows or Wine. The suite covers grouped package
layout, runtime/bootstrap paths, visible profiles, private setup, real GET/WALK/SET,
capture, plotting, scheduling, crypto, and Windows service behavior.

Every archive receives CRC, safe-path, inventory, SHA-256, public-layout, and
Windows executable-format validation. Fixed ZIP/PE timestamps make equivalent
builds deterministic. Reports record failures/errors/skips and the Windows runner.
SCM integration uses mocks on Linux; target Windows deployment instructions are
in the Windows guides.

Check the download hashes with `sha256sum -c SHA256SUMS` in `dist/`, or compare
Windows `Get-FileHash -Algorithm SHA256` output with the checksum inventory.
