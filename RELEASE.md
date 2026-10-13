# Hookah release 1.0.0-2

## Clean, grouped distributions

There are exactly two downloads: **Linux** and **Windows x64**. Each ZIP includes
an `Agent/` folder and a `Manager/` folder for that operating system only.
Each component exposes exactly one executable launcher, `README.md`, `HELP.md`, and
`config.json`. All implementation/support files are grouped in `.hookah/`:

- `app/`: Python application and shared protocol/MIB modules.
- `dependencies/`: runtime requirements.
- `config/`: templates and fresh defaults.
- `docs/`: detailed component, MIB, encryption, and testing guides.
- `platform/`: OS/service integration.
- `metadata/`: version, manifests, and checksums.
- `environment/`: included portable Python with dependencies already installed.

Windows `.exe` entrypoints are native PE32+ x64 console programs, built from
the included C source with standard Windows DLL dependencies. Linux shell
entrypoints preserve executable permissions. The support folder carries hidden
ZIP attributes and is marked hidden by the Windows executable.

Linux packages now expose `hookah-ag` and `hookah-mg` shell commands. Every
operation uses parameters; neither Linux command opens the numbered menu.
The Manager stores multiple Agent addresses and supports adding, selecting,
listing, and removing them. See `LINUX-CLI.md` for commands and PATH installation.
Windows entrypoints and menus are unchanged.

## Artifacts

```text
Hookah-Linux-1.0.0-2.zip
Hookah-Windows-1.0.0-2.zip
SHA256SUMS
VALIDATION.json
```

No component ZIP aliases or mixed Windows/Linux archive are generated. Each ZIP
groups its two ready-to-run applications directly under `Agent/` and `Manager/`.
Source/tests are under `.hookah/source/` and release verification results under
`.hookah/metadata/`. Windows executable caches are not included in the Linux ZIP.

The ZIPs are distributed as GitHub Release assets, not Git blobs: the Linux
Agent/Manager archive exceeds GitHub's 100 MiB Git-file limit. Only these two ZIPs
are uploaded; their SHA-256 digests are included in the release notes.

## Runtime

Invoke the public launcher directly. Portable Python **3.12.15**, Agent modules,
encryption, plotting libraries, and the Manager's PySNMP client backend are
included. No Python/pip/Net-SNMP installation or network download is necessary.
`--setup` on Linux or `setup` on Windows remains an optional offline check and
preserves `config.json`. Windows
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
