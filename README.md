# Hookah 1.0.0-2

Hookah monitors Linux and Windows hosts with an **Agent** on the monitored device
and a **Manager** on the monitoring machine. The Manager reads SNMP metrics,
captures CSV sessions, calculates counter rates, and produces plots. Optional
RSA/AES-GCM transport encrypts requests and responses.

## End-user packages

Choose the package for your application and operating system:

| File in `dist/` | Public launcher |
| --- | --- |
| `Hookah-Agent-Linux-1.0.0-2.zip` | `Hookah-Agent.sh` |
| `Hookah-Agent-Windows-1.0.0-2.zip` | `Hookah-Agent.exe` |
| `Hookah-Manager-Linux-1.0.0-2.zip` | `Hookah-Manager.sh` |
| `Hookah-Manager-Windows-1.0.0-2.zip` | `Hookah-Manager.exe` |
| `Hookah-1.0.0-2.zip` | Both applications, grouped into `Agent/Linux`, `Agent/Windows`, `Manager/Linux`, and `Manager/Windows`. |

Each component ZIP exposes exactly **four files**:

```text
Hookah-Agent.sh       # or the selected component's .exe/.sh
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
The previous unsuffixed Agent/Manager ZIP names and `Hookah-Agent-Windows.zip`
are clean-layout aliases for the Windows variants.

Run the public launcher immediately after extraction: **Python and dependencies
are included**, and no installation or internet connection is needed. The Manager
uses the included PySNMP client backend and includes plotting/encryption modules.
Windows launchers are genuine **x64 PE console executables**; Linux launchers are
shell scripts for x86-64 glibc Linux (glibc 2.28+). `setup` is an optional offline
verification command, and `--snmp-bin` can select existing external Net-SNMP tools.

## Documentation

- Each extracted package has its own `README.md` and `HELP.md`.
- Detailed runtime guides are grouped under `.hookah/docs/`.
- In this developer project: [Agent guide](README-agent.md), [Manager guide](README-manager.md),
  [Windows Agent](WINDOWS-AGENT.md), [Windows Manager](WINDOWS-MANAGER.md),
  [MIB](MIB.md), [encryption](ENCRYPTION.md), and [testing](TESTING.md).

The combined release includes the developer project and tests inside
`Hookah-1.0.0-2/.hookah/source/` and verification results in `.hookah/metadata/`.

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
