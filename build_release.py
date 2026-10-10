#!/usr/bin/env python3
"""Verify Hookah, then build separate Agent/Manager ZIPs and a versioned release."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from hookah_version import __version__


ROOT = Path(__file__).resolve().parent
COMMON_FILES = (
    "hookah_version.py", "snmp_protocol.py", "snmp_mib.py", "snmp_cli_help.py", "snmp_crypto.py",
    "ENCRYPTION.md", "MIB.md", "TESTING.md",
)
AGENT_FILES = COMMON_FILES + (
    "snmp_agent.py", "snmp_agent_windows_service.py", "snmp_agent_config.example.json",
    "requirements-agent.txt", "requirements-windows-agent.txt", "snmp-agent.service",
    "README-agent.md", "WINDOWS-AGENT.md", "hookah-agent", "Hookah-Agent.cmd", "Setup-Agent.cmd",
    "windows/setup-common.ps1", "windows/setup-agent.ps1", "windows/install-agent.ps1", "windows/uninstall-agent.ps1",
)
MANAGER_FILES = COMMON_FILES + (
    "snmp_data_collector_v12.py", "snmp_manager_config.py", "snmp_manager_config.example.json",
    "requirements-manager.txt", "README-manager.md", "WINDOWS-MANAGER.md",
    "hookah-manager", "Hookah-Manager.cmd", "Setup-Manager.cmd",
    "windows/setup-common.ps1", "windows/setup-manager.ps1",
)
SOURCE_FILES = tuple(sorted(set(AGENT_FILES + MANAGER_FILES))) + (
    "README.md", "RELEASE.md", "Makefile", ".gitignore", "requirements-test.txt",
    "build_release.py", "build_agent_package.py", "run_tests.py", "smoke_test.py",
)
EXECUTABLE_FILES = {
    "hookah-agent", "hookah-manager", "snmp_agent.py", "snmp_agent_windows_service.py",
    "snmp_data_collector_v12.py", "build_release.py", "build_agent_package.py", "run_tests.py", "smoke_test.py",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_entries(files, source=ROOT):
    source = Path(source)
    missing = [name for name in files if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing package files: " + ", ".join(missing))
    entries = {}
    for name in files:
        data = (source / name).read_bytes()
        if name.endswith((".cmd", ".ps1")):
            # cmd.exe and Windows PowerShell receive native CRLF text even on a Linux build host.
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        entries[name] = data
    return entries


def component_entries(component, source=ROOT):
    if component not in ("Agent", "Manager"):
        raise ValueError("Choose the Agent or Manager component.")
    entries = source_entries(AGENT_FILES if component == "Agent" else MANAGER_FILES, source)
    template_name = "snmp_agent_config" if component == "Agent" else "snmp_manager_config"
    configuration = json.loads(entries[template_name + ".example.json"])
    if component == "Agent":
        configuration.update(port=1161, read_community="public", write_community=None,
                             allowed_managers=["127.0.0.1/32"], state_file=None,
                             encryption={"enabled": False, "private_key_file": None, "peer_public_key_file": None})
    else:
        configuration.update(target="udp:127.0.0.1:1161", community="public", snmp_bin=None, encryption_config=None)
    # Ship a fresh runnable profile, rather than the developer's edited local configuration.
    entries[template_name + ".json"] = (json.dumps(configuration, indent=2, allow_nan=False) + "\n").encode("utf-8")
    entries["VERSION"] = (__version__ + "\n").encode("ascii")
    return entries


def with_manifest(entries, component):
    entries = dict(entries)
    manifest = {"name": "Hookah", "component": component, "version": __version__,
                "files": {name: sha256(data) for name, data in sorted(entries.items())}}
    entries["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    entries["SHA256SUMS"] = "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(entries.items())).encode("utf-8")
    return entries


def verify_archive(path):
    """Verify CRCs, safe/unique names, every payload hash, and the checksum inventory."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("The ZIP contains duplicate filenames.")
        for name in names:
            if name.startswith("/") or ".." in PurePosixPath(name).parts or "\\" in name or ":" in name:
                raise ValueError(f"Unsafe ZIP member: {name}")
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f"ZIP CRC check failed: {damaged}")
        manifests = [name for name in names if PurePosixPath(name).name == "MANIFEST.json"]
        if len(manifests) != 1:
            raise ValueError("A ZIP must contain exactly one manifest.")
        manifest_path = PurePosixPath(manifests[0])
        prefix = "" if str(manifest_path.parent) == "." else str(manifest_path.parent) + "/"
        manifest = json.loads(archive.read(str(manifest_path)))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            raise ValueError("The ZIP manifest must contain a file inventory.")
        if manifest["version"] != __version__:
            raise ValueError("ZIP release version does not match the application version.")
        expected = {prefix + name for name in manifest["files"]} | {prefix + "MANIFEST.json", prefix + "SHA256SUMS"}
        if set(names) != expected:
            raise ValueError("ZIP contents do not match its manifest inventory.")
        for name, digest in manifest["files"].items():
            if sha256(archive.read(prefix + name)) != digest:
                raise ValueError(f"ZIP SHA-256 check failed: {name}")
        checksums = "".join(f"{sha256(archive.read(name))}  {name[len(prefix):]}\n"
                            for name in sorted(expected - {prefix + "SHA256SUMS"}))
        if archive.read(prefix + "SHA256SUMS").decode("utf-8") != checksums:
            raise ValueError("The ZIP checksum inventory does not match its contents.")
        return manifest


def write_archive(output, entries, component, *, prefix=""):
    output = Path(output).expanduser().resolve()
    entries = with_manifest(entries, component)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False) as destination:
            temporary = Path(destination.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(prefix + name, date_time=ZIP_TIMESTAMP)
                info.create_system = 3
                mode = 0o755 if PurePosixPath(name).name in EXECUTABLE_FILES else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
        verify_archive(temporary)
        temporary.replace(output)
        return output
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_component(component, output, source=ROOT):
    return write_archive(output, component_entries(component, source), component)


def validate_test_reports(validation):
    if not isinstance(validation, dict) or validation.get("version") != __version__:
        raise ValueError("Release packaging requires matching verification results.")
    for suite in ("unit", "integration", "smoke"):
        report = validation.get(suite, {})
        if report.get("status") != "passed" or report.get("version") != __version__:
            raise ValueError(f"Release packaging requires a passing {suite} verification report.")
        count = len(report.get("checks", [])) if suite == "smoke" else report.get("tests_run", 0)
        if type(count) is not int or count <= 0 or report.get("failures") or report.get("errors"):
            raise ValueError(f"Release packaging requires successful, nonempty {suite} checks.")
        if suite != "smoke" and count <= len(report.get("skipped", [])):
            raise ValueError(f"Release packaging requires executed {suite} checks, not an entirely skipped suite.")


def build_release(output_directory, validation, source=ROOT):
    validate_test_reports(validation)
    source, output_directory = Path(source), Path(output_directory).expanduser().resolve()
    # Validate all inputs before creating the release artifacts.
    project = source_entries(SOURCE_FILES, source)
    for test in sorted((source / "tests").glob("*.py")):
        project["tests/" + test.name] = test.read_bytes()
    agent = component_entries("Agent", source)
    manager = component_entries("Manager", source)
    for name in ("snmp_agent_config.json", "snmp_manager_config.json"):
        project[name] = (agent if name.startswith("snmp_agent") else manager)[name]
    project["VERSION"] = (__version__ + "\n").encode("ascii")
    packages = []
    for component, entries in (("Agent", agent), ("Manager", manager)):
        packages.append(write_archive(output_directory / f"Hookah-{component}-{__version__}.zip", entries, component))
    validation_bytes = (json.dumps(validation, indent=2) + "\n").encode("utf-8")
    bundle = {package.name: package.read_bytes() for package in packages}
    for name in ("README.md", "README-agent.md", "README-manager.md", "WINDOWS-AGENT.md", "WINDOWS-MANAGER.md",
                 "ENCRYPTION.md", "MIB.md", "RELEASE.md", "TESTING.md"):
        bundle[name] = project[name]
    bundle["VALIDATION.json"] = validation_bytes
    bundle["VERSION"] = project["VERSION"]
    bundle.update({"source/" + name: data for name, data in project.items()})
    packages.append(write_archive(output_directory / f"Hookah-{__version__}.zip", bundle, "Release", prefix=f"Hookah-{__version__}/"))
    (output_directory / "VALIDATION.json").write_bytes(validation_bytes)
    checksums = "".join(f"{sha256(package.read_bytes())}  {package.name}\n" for package in packages)
    (output_directory / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    return packages


def run_verification(source=ROOT, python=sys.executable):
    source = Path(source).resolve()
    reports = {"version": __version__, "native_windows_execution": sys.platform == "win32"}
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", MPLBACKEND="Agg", PYTHONUTF8="1")
    with tempfile.TemporaryDirectory(prefix="hookah-release-checks-") as directory:
        for suite in ("unit", "integration", "smoke"):
            report = Path(directory) / f"{suite}.json"
            arguments = [python, "-B", str(source / ("smoke_test.py" if suite == "smoke" else "run_tests.py"))]
            if suite != "smoke":
                arguments.extend(("--suite", suite))
            arguments.extend(("--report", str(report)))
            print(f"Running {suite} verification...", flush=True)
            subprocess.run(arguments, cwd=source, env=environment, check=True, timeout=300)
            reports[suite] = json.loads(report.read_text(encoding="utf-8"))
    validate_test_reports(reports)
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=ROOT / "dist")
    parser.add_argument("--python", default=sys.executable, help="Python environment with requirements-test.txt installed")
    args = parser.parse_args(argv)
    try:
        source_entries(SOURCE_FILES)
        validation = run_verification(python=args.python)
        for package in build_release(args.output_directory, validation):
            print(package)
        print(f"Release {__version__} verified; SHA256SUMS and VALIDATION.json written.")
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
