# Hookah release 1.0.0

## Highlights

- Linux-friendly Manager entrypoint, clean automation output, distro-specific
  dependency guidance, configuration profiles, diagnostic checks, and exit codes.
- Prompt-free PNG plotting/comparison for headless Linux and SSH sessions.
- Windows `.cmd` foreground launchers and per-component PowerShell setup, plus
  the native Agent auto-start service installer.
- Independent Agent/Manager ZIPs with their guides, runnable configuration,
  requirements, application version, file manifests, and SHA-256 checksums.
- A combined versioned release with both distributions, full source, and
  machine-readable validation results.

## Artifacts

```text
Hookah-Agent-1.0.0.zip
Hookah-Manager-1.0.0.zip
Hookah-1.0.0.zip
SHA256SUMS
VALIDATION.json
```

The combined ZIP extracts to `Hookah-1.0.0/`. Extract the component ZIPs inside
into separate Agent and Manager directories. The `source/` directory contains
the full project and tests for verification or rebuilding.

Standalone configurations use UDP 1161 and matching `public` read defaults for
a quick loopback trial. The Agent permits only loopback initially. Set the real
Manager address and communities when deploying across the LAN. Profiles are
generated from examples, with encryption disabled until keys are configured.
Python 3.10+ and the component requirements are installed on the target host;
Net-SNMP clients are required on the Manager machine.

## Verification before packaging

`make release` executes, in order:

1. Unit tests for Agent/Manager parsing, configuration, collectors, scheduling,
   counters/units, CSVs/plots, crypto, Windows service/setup/launch support, and
   release packaging validation.
2. Real loopback SNMPv1/v2c and encrypted integration tests, access and SET
   restrictions, persistence, live configuration, and synchronized capture.
3. A short smoke test for JSON-profile GET, WALK, CPU/RAM aggregation, SET and
   read-back, bounded capture, headless plot/comparison, and remote configuration.
4. ZIP CRC, filename, manifest, and SHA-256 integrity checks.

Failing or empty verification suites stop release generation. ZIP payloads use
fixed timestamps and deterministic ordering. Windows scripts are packaged with
CRLF line endings; Linux executable metadata is preserved. Environments, caches,
generated private keys, and local device settings are excluded from the payload.

`VALIDATION.json` records actual test counts, failures/errors/skips, Python/OS,
smoke checks, and whether the verification ran on native Windows. Linux builds
verify PowerShell and mocked Windows service behavior; `.cmd`/SCM deployment is
also checked on the destination Windows host as described in the Windows guides.

On Linux, check the download hashes with `sha256sum -c SHA256SUMS` in `dist/`.
On Windows, compare `Get-FileHash -Algorithm SHA256` output with the listed hashes.
