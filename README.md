# Hookah 1.0.0-2

Hookah monitors Linux and Windows hosts with an **Agent** on the monitored device
and a **Manager** on the monitoring machine. The Manager reads SNMP metrics,
captures CSV sessions, calculates counter rates, and produces plots. Optional
RSA/AES-GCM transport encrypts requests and responses.

## End-user packages

Download the archive for your operating system. Each contains both applications:

| File in `dist/` | Agent and Manager launchers |
| --- | --- |
| `Hookah-Linux-1.0.0-2.zip` | `Agent/hookah-ag`, `Manager/hookah-mg` |
| `Hookah-Windows-1.0.0-2.zip` | `Agent/Hookah-Agent.exe`, `Manager/Hookah-Manager.exe` |

There are exactly two release ZIPs, with no mixed-OS archive or component aliases.
Inside each archive, the `Agent/` and `Manager/` folders each expose **four files**:

```text
hookah-ag            # hookah-mg for Manager; the selected .exe on Windows
README.md
HELP.md
config.json
.hookah/
  app/               Python implementation
  dependencies/      dependency lists
  config/            templates and fresh defaults
  docs/              detailed guides
  platform/          service/OS integration files
  metadata/          version, manifests, checksums
```

The support folder is hidden on Linux, has hidden ZIP attributes, and is marked
hidden by the native Windows launcher. `.hookah/environment/` already contains
portable Python and all required modules.
The complete component folder can be copied to another device running the same
operating system.

Run the public launcher immediately after extraction: **Python and dependencies
are included**, and no installation or internet connection is needed. The Manager
uses the included PySNMP client backend and includes plotting/encryption modules.
Windows launchers are genuine **x64 PE console executables**; Linux launchers are
shell commands for x86-64 glibc Linux (glibc 2.28+). `--setup` on Linux or `setup`
on Windows is an optional offline
verification command, and `--snmp-bin` can select existing external Net-SNMP tools.

## Documentation

- Linux packages use parameter-only commands, saved Agent lists, and optional PATH installation: [Linux CLI](LINUX-CLI.md).
- `hookah-ag` starts the Agent; `hookah-mg` prints parameter help. Windows menus and commands remain unchanged.
- Each extracted package has its own `README.md` and `HELP.md`.
- Detailed runtime guides are grouped under `.hookah/docs/`.
- In this developer project: [Agent guide](README-agent.md), [Manager guide](README-manager.md),
  [Windows Agent](WINDOWS-AGENT.md), [Windows Manager](WINDOWS-MANAGER.md),
  [MIB](MIB.md), [encryption](ENCRYPTION.md), and [testing](TESTING.md).

Each platform release includes the developer project and tests in `.hookah/source/`
and verification results in `.hookah/metadata/`. Windows compiled launcher caches
are included only in the Windows archive. Download ZIPs from this repository's
GitHub Releases; the Linux bundle exceeds GitHub's 100 MiB Git-file limit.

## Build a verified release

On Debian/Ubuntu, install build/test prerequisites, then:

```bash
sudo apt install python3-venv snmp gcc-mingw-w64-x86-64
make setup-test
make release
```

`HOOKAH_WINDOWS_CC=/path/to/x86_64-w64-mingw32-gcc` selects another MinGW compiler.
Native launcher builds are cached under `build/windows/` against source hashes.
`make runtime-bundles` downloads checksum-pinned Python distributions and binary
wheels on the build machine, caching their already-installed runtime payloads in
`build/bundles/`. End-user machines do not perform these downloads. Runtime and
third-party license notices are retained in the bundled environment.
Unit tests, real loopback integration tests, and a smoke test run before ZIP
creation. Windows launchers are also executed when Windows or Wine is available.
Archives are deterministic, preserve Unix executable modes/Windows hidden flags,
and undergo CRC, path, inventory, SHA-256, and executable-format verification.

Made by **0moorii**.
