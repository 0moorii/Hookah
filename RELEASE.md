# Hookah release 1.0.0-2

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
- `environment/`: included portable Python with dependencies already installed.

Windows `.exe` entrypoints are native PE32+ x64 console programs, built from
the included C source with standard Windows DLL dependencies. Linux `.sh`
entrypoints preserve executable permissions. The support folder carries hidden
ZIP attributes and is marked hidden by the Windows executable.

## Artifacts

```text
Hookah-Agent-Linux-1.0.0-2.zip
Hookah-Agent-Windows-1.0.0-2.zip
Hookah-Manager-Linux-1.0.0-2.zip
Hookah-Manager-Windows-1.0.0-2.zip
Hookah-1.0.0-2.zip
SHA256SUMS
VALIDATION.json
```

The previous `Hookah-Agent-1.0.0-2.zip`, `Hookah-Manager-1.0.0-2.zip`, and
`Hookah-Agent-Windows.zip` names are refreshed Windows-package aliases.
The combined ZIP groups ready-to-run folders as `Agent/Linux`, `Agent/Windows`,
`Manager/Linux`, and `Manager/Windows`. Source/tests are under `.hookah/source/`
and release verification results under `.hookah/metadata/`.

## Runtime

Invoke the public launcher directly. Portable Python **3.12.15**, Agent modules,
encryption, plotting libraries, and the Manager's PySNMP client backend are
included. No Python/pip/Net-SNMP installation or network download is necessary.
`setup` remains an optional offline check and preserves `config.json`. Windows
Agent bundles also include pywin32, allowing service installation without
downloading dependencies. Linux targets x86-64 glibc systems with glibc 2.28+.
Arguments, Unicode paths, spaces, quotes, and exit statuses are preserved.

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

Isolated bundle tests block system Python, disable pip downloads, and verify
startup, diagnostics, SNMP operations, capture, plotting, and key generation
using the supplied runtimes. Windows executable tests run under Wine on Linux.

Check the download hashes with `sha256sum -c SHA256SUMS` in `dist/`, or compare
Windows `Get-FileHash -Algorithm SHA256` output with the checksum inventory.
