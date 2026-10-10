#!/usr/bin/env python3
"""Build the transferable Windows Agent installation ZIP."""

import argparse
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PACKAGE_FILES = (
    "snmp_agent.py", "snmp_protocol.py", "snmp_mib.py", "snmp_cli_help.py", "snmp_crypto.py", "snmp_agent_windows_service.py",
    "requirements-agent.txt", "requirements-windows-agent.txt", "snmp_agent_config.example.json",
    "windows/install-agent.ps1", "windows/uninstall-agent.ps1", "WINDOWS-AGENT.md", "ENCRYPTION.md", "MIB.md",
)


def build_package(output, source=ROOT):
    output, source = Path(output).expanduser().resolve(), Path(source)
    missing = [name for name in PACKAGE_FILES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing package files: " + ", ".join(missing))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in PACKAGE_FILES:
            archive.write(source / name, name)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "Hookah-Agent-Windows.zip")
    args = parser.parse_args(argv)
    try:
        print(build_package(args.output))
    except OSError as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
