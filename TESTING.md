# Agent and Manager testing environment

Use the source project, or `Hookah-1.0.0/source/` from the combined release ZIP.
Standalone component ZIPs contain runtime files and deployment guides; the full
source release supplies the tests and release builder.

Python 3.10+ is required. Tests use standard-library **unittest**, PySNMP, psutil,
cryptography, and Matplotlib for headless manager plot verification. Real integration tests
also need Net-SNMP clients. On Debian/Ubuntu:

```bash
sudo apt install python3-venv snmp make
```

## Set up and run

From the project directory:

```bash
make setup-test
make test
```

This creates a dedicated `.venv-test` environment using
`requirements-test.txt`. Select an individual suite with:

```bash
make test-unit
make test-integration
make smoke-test
```

The executable runner can also be used directly:

```bash
source .venv-test/bin/activate
./run_tests.py --suite all
./run_tests.py --suite unit
./run_tests.py --suite integration
```

On Windows, create `.venv-test` with `py -3 -m venv .venv-test`, install
`requirements-test.txt` using `.venv-test\Scripts\python.exe -m pip`, and invoke
the runner with that Python executable. The direct Unix shebang/executable and
named-loopback integration checks target Linux.

The runner returns a nonzero exit status on failure and checks required tools
before integration runs. It uses the Agg plot backend and a temporary Matplotlib
cache. Agents bind dynamically selected unprivileged loopback UDP ports;
configurations, captured CSVs, persisted settings, and PNGs are temporary.

`./hookah-manager doctor` diagnoses the installed Manager's dependencies without
starting an Agent. `smoke_test.py` starts a disposable loopback Agent and checks
the real Manager's profile-based GET/WALK, CPU/RAM aggregation, authorized SET and
read-back, short synchronized capture, headless plot/comparison, and remote Agent
configuration. No listening service or device configuration is changed.

## Verified release

```bash
make release
# Equivalent, after activating the test environment:
python -B build_release.py --output-directory dist
```

The builder runs unit and integration suites and the smoke test before writing
the versioned Agent, Manager, and combined release ZIPs. It checks every archive's
CRC and SHA-256 manifest and writes `dist/VALIDATION.json` plus `dist/SHA256SUMS`.
Run `sha256sum -c SHA256SUMS` inside `dist/` to verify the standalone downloads.
The individual runner accepts `--report PATH` to save its actual results as JSON.

## Coverage

| Tests | Verified behavior |
| --- | --- |
| `tests/test_agent_unit.py` | CLI configuration creation/editing, seconds-only validation, atomic edits/permissions, independent sampling schedules, cached counters, live reload, large RAM values, and configuration SET validation. |
| `tests/test_manager_unit.py` | SNMP parsing and Counter64 precision, rates/wraparound, CPU/RAM helpers, CSVs, headless plots, bounded capture duration, and following interval changes. |
| `tests/test_snmp_agent.py` | Real SNMPv1/v2c GET/WALK/GETBULK/SET, manager CPU/RAM and traffic capture, access restrictions, system-value persistence, and rollover-compatible data. |
| `tests/test_runtime_configuration.py` | Executable programs, remote and local/SSH-style CLI edits, hot reload while waiting 120 seconds, persistence across restart, independent counter caches, configuration authorization, and a concurrently running manager adapting to remote changes. |
| `tests/test_crypto_unit.py` | RSA key generation/configuration, AES-GCM roundtrips, signatures, tamper/replay/wrong-key rejection, and the Shisha/Hookah startup banner. |
| `tests/test_encrypted_integration.py` | Encrypted SNMPv1/v2c GET/WALK/SET, synchronized capture, remote configuration, wire inspection, plaintext rejection, and service-loop shutdown. |
| `tests/test_windows_service_unit.py` | Mocked native Windows service installation/lifecycle, registry configuration, paths with spaces, rotating logs, and Windows interface-data compatibility. |
| `tests/test_extended_mib.py` | Shared tree/catalog integrity, descriptive help, disk/process/address/connection/swap/sensor/service collectors, gauges versus counters, and readable unit conversion. |
| `tests/test_manager_cli.py` | Linux CLI/version/help/banner behavior, configuration precedence/validation, commands/defaults/overrides, dependency diagnostics, error/interrupt/EOF/pipe exit behavior, and prompt-free plots. |
| `tests/test_release_unit.py` | Standalone/bundled payloads, runnable defaults, Windows line endings, executable modes, deterministic builds, checksum/manifest corruption detection, missing-file handling, and verification-before-packaging gates. |
| `tests/test_launchers.py` | Linux launchers from paths with spaces/another working directory, PowerShell syntax/setup helpers, and native Windows `.cmd` launch checks when running on Windows. |

Unit tests advance virtual monotonic clocks to verify exact **1**, **10**, and
**120** second boundaries without waiting two minutes. Live integration tests
verify the same configuration values over SNMP and use short fractional-second
periods for rapid collection/capture checks.

Windows service lifecycle tests mock native APIs when run on Linux; actual SCM
activation requires a Windows device. Install `requirements-windows-agent.txt`
there and follow `WINDOWS-AGENT.md` for the service smoke check. Optional encrypted
transport setup is in `ENCRYPTION.md`.

## Run Agent and Manager together

After activating `.venv-test`, start the agent in one terminal:

```bash
./snmp_agent.py --config snmp_agent_config.json
```

The supplied configuration uses port 1161, public read access from loopback, and
a one-second default collection period. Discover an interface index:

```bash
./snmp_data_collector_v12.py walk --target udp:127.0.0.1:1161 \
  --oid .1.3.6.1.2.1.2.2.1.2
```

Use the discovered index (shown as `1` below), then capture from another terminal:

```bash
./snmp_data_collector_v12.py capture --target udp:127.0.0.1:1161 \
  --oid .1.3.6.1.2.1.2.2.1.10.1 --sync-agent --duration 30 --output incoming.csv
```

Change that metric's collection period through the local CLI:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 10
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 120
./snmp_agent.py config set --config snmp_agent_config.json --interval ifInOctets 1
```

The agent reloads sampling settings and the `--sync-agent` manager follows them.
GET requests between collections return the cached counter. `120` is a seconds
value; no minutes/hours notation is accepted.

For updates over SNMP from another device, see **Remotely change configuration**
in `README-agent.md`. The remote device must be allowed and use the configured
write community. Sampling changes are applied live; enabling/changing a
community requires one agent restart.
