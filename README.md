# Hookah 1.0.0

Hookah monitors Linux and Windows hosts with an SNMP **Agent** on the monitored
device and a **Manager** on the monitoring machine. The Manager reads metrics,
captures CSV sessions, calculates counter rates, and produces plots. Optional
RSA/AES-GCM transport encrypts requests and responses.

- [Agent installation and configuration](README-agent.md)
- [Manager CLI and Linux installation](README-manager.md)
- [Windows Agent and service](WINDOWS-AGENT.md)
- [Windows Manager](WINDOWS-MANAGER.md)
- [Shared MIB and OIDs](MIB.md)
- [Encryption](ENCRYPTION.md)
- [Tests](TESTING.md) and [release information](RELEASE.md)

## Release downloads

| File | Contents |
| --- | --- |
| `dist/Hookah-Agent-1.0.0.zip` | Agent, editable/default configuration, Agent guides, Linux/Windows launchers, Windows setup/service scripts, requirements, version and checksums. |
| `dist/Hookah-Manager-1.0.0.zip` | Manager, editable/default configuration, Manager guides, Linux/Windows launchers, Windows setup script, requirements, version and checksums. |
| `dist/Hookah-1.0.0.zip` | Both standalone ZIPs, guides, validation results, and the complete runnable/testable project under `Hookah-1.0.0/source/`. |

Extract each standalone ZIP into its own directory. On Windows, install Python
3.10+, run `Setup-Agent.cmd` or `Setup-Manager.cmd`, then use `Hookah-Agent.cmd`
or `Hookah-Manager.cmd`. The Manager also requires Net-SNMP command-line clients.
These are Python distributions with executable launchers; the Python runtime and
third-party dependencies are installed by setup.

On Linux, follow the component README to create a virtual environment, then run
`./hookah-agent` or `./hookah-manager`. ZIP entries preserve Unix executable
permissions; an extractor that discards them can be followed by `chmod +x` on
the launcher and main Python script.

## Build a verified release

From the source project, with Net-SNMP installed:

```bash
make setup-test
make release
```

The release command runs unit tests, real loopback integration tests, and a
short smoke test **before** producing the three archives. Each archive has a
`MANIFEST.json`, `VERSION`, and `SHA256SUMS`; the output directory also contains
`SHA256SUMS` and `VALIDATION.json`. Local configurations are replaced with fresh
example-based defaults in the distributions.

Made by **0moorii**.
