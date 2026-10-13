#!/usr/bin/env python3
"""Verify Hookah, then build one Agent/Manager release ZIP per operating system."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from hookah_version import __version__
from release_tools.windows import ensure_windows_launchers, inspect_pe
from release_tools.bundles import ensure_bundle, ensure_bundles


ROOT = Path(__file__).resolve().parent
COMMON_FILES = (
    "hookah_version.py", "snmp_protocol.py", "snmp_mib.py", "snmp_cli_help.py", "snmp_crypto.py",
    "ENCRYPTION.md", "MIB.md", "TESTING.md",
)
AGENT_FILES = COMMON_FILES + (
    "snmp_agent.py", "snmp_agent_windows_service.py", "snmp_agent_config.example.json",
    "requirements-agent.txt", "requirements-windows-agent.txt", "snmp-agent.service",
    "README-agent.md", "WINDOWS-AGENT.md", "windows/install-agent.ps1", "windows/uninstall-agent.ps1",
)
MANAGER_FILES = COMMON_FILES + (
    "snmp_data_collector_v12.py", "snmp_python_client.py", "snmp_manager_config.py", "snmp_manager_config.example.json",
    "requirements-manager.txt", "README-manager.md", "WINDOWS-MANAGER.md",
)
SOURCE_FILES = tuple(sorted(set(AGENT_FILES + MANAGER_FILES))) + (
    "README.md", "RELEASE.md", "Makefile", ".gitignore", "requirements-test.txt",
    "build_release.py", "build_agent_package.py", "run_tests.py", "smoke_test.py",
    "hookah-agent", "hookah-manager", "Hookah-Agent.cmd", "Hookah-Manager.cmd", "Setup-Agent.cmd", "Setup-Manager.cmd",
    "windows/setup-common.ps1", "windows/setup-agent.ps1", "windows/setup-manager.ps1",
    "LINUX-CLI.md",
)
EXECUTABLE_FILES = {
    "hookah-agent", "hookah-manager", "snmp_agent.py", "snmp_agent_windows_service.py",
    "snmp_data_collector_v12.py", "build_release.py", "build_agent_package.py", "run_tests.py", "smoke_test.py",
    "hookah-ag", "hookah-mg",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
METADATA_DIRECTORY = ".hookah/metadata"
PLATFORMS = ("Linux", "Windows")


def entry_bytes(data):
    return data.read_bytes() if isinstance(data, Path) else data


def sha256(data):
    if isinstance(data, Path):
        digest = hashlib.sha256()
        with data.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
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


def launcher_name(component, platform):
    if platform == "Linux":
        return {"Agent": "hookah-ag", "Manager": "hookah-mg"}.get(component, f"Hookah-{component}.sh")
    return f"Hookah-{component}.exe"


def render_document(name, component, platform, source=ROOT):
    launcher = launcher_name(component, platform)
    command = ("./" if platform == "Linux" else ".\\") + launcher
    if platform == "Linux" and component in ("Agent", "Manager"):
        name = name.removesuffix(".md") + "-Linux.md"
    text = (Path(source) / "release_tools" / "docs" / name).read_text(encoding="utf-8")
    for token, value in {"@VERSION@": __version__, "@PLATFORM@": platform,
                         "@LAUNCHER@": launcher, "@COMMAND@": command,
                         "@AGENT_LAUNCHER@": launcher_name("Agent", platform),
                         "@MANAGER_LAUNCHER@": launcher_name("Manager", platform),
                         "@AGENT_COMMAND@": ("./Agent/" if platform == "Linux" else ".\\Agent\\") + launcher_name("Agent", platform),
                         "@MANAGER_COMMAND@": ("./Manager/" if platform == "Linux" else ".\\Manager\\") + launcher_name("Manager", platform),
                         "@SETUP@": "--setup" if platform == "Linux" else "setup",
                         "@DOCTOR@": "--doctor" if platform == "Linux" else "doctor",
                         "@VERSION_FLAG@": "-v" if platform == "Linux" else "--version"}.items():
        text = text.replace(token, value)
    return text.encode("utf-8")


def component_entries(component, source=ROOT, platform="Windows", *, bundle_runtime=True):
    if component not in ("Agent", "Manager"):
        raise ValueError("Choose the Agent or Manager component.")
    if platform not in PLATFORMS:
        raise ValueError("Choose the Linux or Windows platform.")
    original = source_entries(AGENT_FILES if component == "Agent" else MANAGER_FILES, source)
    entries = {}
    for name, data in original.items():
        if name.endswith(".py"):
            destination = ".hookah/app/" + name
        elif name.endswith(".md"):
            destination = ".hookah/docs/" + name
        elif name.endswith(".json"):
            destination = ".hookah/config/" + name
        elif name.endswith(".txt"):
            destination = ".hookah/dependencies/" + name
        elif name.startswith("windows/"):
            destination = ".hookah/platform/" + name
        else:
            destination = ".hookah/platform/linux/" + name
        entries[destination] = data
    template_name = "snmp_agent_config" if component == "Agent" else "snmp_manager_config"
    configuration = json.loads(original[template_name + ".example.json"])
    if component == "Agent":
        configuration.update(port=1161, read_community="public", write_community=None,
                             allowed_managers=["127.0.0.1/32"], state_file=None,
                             encryption={"enabled": False, "private_key_file": None, "peer_public_key_file": None})
    else:
        configuration.update(target="udp:127.0.0.1:1161", community="public", snmp_bin=None, encryption_config=None)
    # Ship a fresh runnable profile, rather than the developer's edited local configuration.
    entries["config.json"] = (json.dumps(configuration, indent=2, allow_nan=False) + "\n").encode("utf-8")
    entries[".hookah/config/default.json"] = entries["config.json"]
    entries[".hookah/launcher.py"] = (Path(source) / "release_tools" / "runtime.py").read_bytes()
    entries[METADATA_DIRECTORY + "/VERSION"] = (__version__ + "\n").encode("ascii")
    metadata = {"component": component, "platform": platform, "version": __version__, "bundled_runtime": bundle_runtime}
    entries[METADATA_DIRECTORY + "/component.json"] = (json.dumps(metadata, indent=2) + "\n").encode("utf-8")
    entries["README.md"] = render_document(f"README-{component}.md", component, platform, source)
    entries["HELP.md"] = render_document(f"HELP-{component}.md", component, platform, source)
    entries[".hookah/docs/THIRD-PARTY.md"] = (Path(source) / "release_tools/docs/THIRD-PARTY.md").read_bytes()
    if platform == "Linux":
        launcher = launcher_name(component, platform)
        entries[".hookah/app/hookah_runtime.py"] = entries[".hookah/launcher.py"]
        entries[".hookah/launcher.py"] = (Path(source) / "release_tools/linux_cli.py").read_bytes()
        entries[".hookah/docs/LINUX-CLI.md"] = (Path(source) / "LINUX-CLI.md").read_bytes()
        guide = ".hookah/docs/README-" + component.lower() + ".md"
        entries[guide] = (f"> Linux packages use `{launcher}` parameters. See `LINUX-CLI.md` for the current Linux interface; "
                         "direct-script and Windows examples below remain available in their respective environments.\n\n").encode("utf-8") + entries[guide]
        if component == "Manager":
            entries[".hookah/config/agents.json"] = b'{"agents": []}\n'
        template = (Path(source) / "release_tools" / "launchers" / "linux.sh.in").read_text(encoding="utf-8")
        entries[launcher] = template.replace("@LAUNCHER@", launcher).encode("utf-8")
    else:
        binary = ensure_windows_launchers(source)[component]
        entries[f"Hookah-{component}.exe"] = binary.read_bytes()
        inspect_pe(entries[f"Hookah-{component}.exe"])
    if bundle_runtime:
        bundle = ensure_bundle(component, platform, source)
        excluded = []
        for path in sorted((bundle / "environment").rglob("*")):
            if path.is_file():
                relative = path.relative_to(bundle / "environment").as_posix()
                if platform == "Linux" and path.suffix.lower() == ".exe":
                    excluded.append(relative)
                    continue
                entries[".hookah/environment/" + relative] = path
        metadata = (bundle / "BUNDLE.json").read_bytes()
        if excluded:
            record = json.loads(metadata)
            # Pip's Windows launcher resources are not used by the Linux interpreter.
            record["files"] = {name: digest for name, digest in record["files"].items() if name not in excluded}
            record["executables"] = [name for name in record.get("executables", []) if name not in excluded]
            record["excluded_windows_launchers"] = excluded
            metadata = (json.dumps(record, indent=2) + "\n").encode("utf-8")
        entries[METADATA_DIRECTORY + "/BUNDLE.json"] = metadata
    return entries


def with_manifest(entries, component, platform=None):
    entries = dict(entries)
    # Explicit hidden directory entries are understood by Windows ZIP extractors.
    for name in tuple(entries) + (METADATA_DIRECTORY + "/MANIFEST.json",):
        for parent in PurePosixPath(name).parents:
            if str(parent) != ".":
                entries.setdefault(str(parent) + "/", b"")
    manifest = {"name": "Hookah", "component": component, "version": __version__, "platform": platform, "layout_version": 2,
                "files": {name: sha256(data) for name, data in sorted(entries.items())}}
    entries[METADATA_DIRECTORY + "/MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    entries[METADATA_DIRECTORY + "/SHA256SUMS"] = "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(entries.items())).encode("utf-8")
    return entries


def verify_manifest(archive, manifest_path):
    manifest_path = PurePosixPath(manifest_path)
    parent = manifest_path.parents[2]
    prefix = "" if str(parent) == "." else str(parent) + "/"
    manifest = json.loads(archive.read(str(manifest_path)))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("The ZIP manifest must contain a file inventory.")
    if manifest["version"] != __version__:
        raise ValueError("ZIP release version does not match the application version.")
    checksum_path = prefix + METADATA_DIRECTORY + "/SHA256SUMS"
    expected = {prefix + name for name in manifest["files"]} | {str(manifest_path), checksum_path}
    names = {name for name in archive.namelist() if name.startswith(prefix) and name != prefix}
    if names != expected:
        raise ValueError("ZIP contents do not match its manifest inventory.")
    for name, digest in manifest["files"].items():
        if sha256(archive.read(prefix + name)) != digest:
            raise ValueError(f"ZIP SHA-256 check failed: {name}")
    checksums = "".join(f"{sha256(archive.read(name))}  {name[len(prefix):]}\n"
                        for name in sorted(expected - {checksum_path}))
    if archive.read(checksum_path).decode("utf-8") != checksums:
        raise ValueError("The ZIP checksum inventory does not match its contents.")
    return manifest, prefix


def verify_component_layout(archive, manifest, prefix):
    component, platform = manifest["component"], manifest["platform"]
    names = {name for name in archive.namelist() if name.startswith(prefix) and name != prefix}
    launcher = launcher_name(component, platform)
    root_files = {name[len(prefix):] for name in names if "/" not in name[len(prefix):]}
    if root_files != {launcher, "README.md", "HELP.md", "config.json"}:
        raise ValueError("A component ZIP must expose only its launcher, README, help, and config.")
    if any(name.endswith(".py") and not name[len(prefix):].startswith(".hookah/") for name in names):
        raise ValueError("Python implementation files must stay inside the support folder.")
    metadata = json.loads(archive.read(prefix + METADATA_DIRECTORY + "/component.json"))
    if any(metadata.get(name) != manifest.get(name) for name in ("component", "platform", "version")):
        raise ValueError("Component metadata does not match its manifest.")
    if platform == "Windows":
        inspect_pe(archive.read(prefix + launcher))
    if prefix + METADATA_DIRECTORY + "/BUNDLE.json" in names:
        bundle = json.loads(archive.read(prefix + METADATA_DIRECTORY + "/BUNDLE.json"))
        interpreter = "python.exe" if platform == "Windows" else "bin/python"
        if prefix + ".hookah/environment/" + interpreter not in names:
            raise ValueError("The bundled Python interpreter is missing.")
        for name, digest in bundle["files"].items():
            if manifest["files"].get(".hookah/environment/" + name) != digest:
                raise ValueError("The packaged runtime does not match its dependency inventory.")


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
        manifests = [PurePosixPath(name) for name in names if name.endswith(METADATA_DIRECTORY + "/MANIFEST.json")]
        if not manifests:
            raise ValueError("A ZIP must contain a hidden package manifest.")
        shallowest = min(len(path.parts) for path in manifests)
        manifests = [path for path in manifests if len(path.parts) == shallowest]
        if len(manifests) != 1:
            raise ValueError("A ZIP must contain exactly one root manifest.")
        manifest, prefix = verify_manifest(archive, manifests[0])
        platform = manifest.get("platform")
        if platform in PLATFORMS and manifest.get("component") in ("Agent", "Manager"):
            verify_component_layout(archive, manifest, prefix)
        elif platform in PLATFORMS and manifest.get("component") == "Release":
            roots = {PurePosixPath(name[len(prefix):]).parts[0] for name in names if name.startswith(prefix) and name != prefix}
            if roots != {"Agent", "Manager", "README.md", "HELP.md", ".hookah"}:
                raise ValueError("A platform release must contain only Agent, Manager, README, HELP, and hidden support files.")
            for component in ("Agent", "Manager"):
                child, child_prefix = verify_manifest(archive, prefix + component + "/" + METADATA_DIRECTORY + "/MANIFEST.json")
                if child.get("component") != component or child.get("platform") != platform:
                    raise ValueError("A platform release cannot mix Linux and Windows packages.")
                verify_component_layout(archive, child, child_prefix)
            if platform == "Linux" and any(name.lower().endswith(".exe") for name in names):
                raise ValueError("A Linux release cannot include Windows executables.")
        return manifest


def write_archive(output, entries, component, *, prefix="", platform=None):
    output = Path(output).expanduser().resolve()
    executables = set()
    for name, data in entries.items():
        if name.endswith(METADATA_DIRECTORY + "/BUNDLE.json"):
            metadata = json.loads(entry_bytes(data))
            root = name.removesuffix(METADATA_DIRECTORY + "/BUNDLE.json")
            executables.update(root + ".hookah/environment/" + path for path in metadata.get("executables", []))
    entries = with_manifest(entries, component, platform)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False) as destination:
            temporary = Path(destination.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(prefix + name, date_time=ZIP_TIMESTAMP)
                info.create_system = 3
                executable = name in executables or name.endswith((".sh", ".exe")) or PurePosixPath(name).name in EXECUTABLE_FILES
                mode = 0o755 if executable or name.endswith("/") else 0o644
                info.external_attr = ((stat.S_IFDIR if name.endswith("/") else stat.S_IFREG) | mode) << 16
                if name.endswith("/"):
                    info.external_attr |= 0x10
                if ".hookah" in PurePosixPath(name).parts:
                    info.external_attr |= 0x02
                info.compress_type = zipfile.ZIP_DEFLATED
                if isinstance(data, Path):
                    with data.open("rb") as input_file, archive.open(info, "w", force_zip64=True) as output_file:
                        shutil.copyfileobj(input_file, output_file)
                else:
                    archive.writestr(info, data, compresslevel=9)
        verify_archive(temporary)
        temporary.replace(output)
        return output
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_component(component, output, source=ROOT, platform="Windows"):
    return write_archive(output, component_entries(component, source, platform), component, platform=platform)


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
    for folder in ("release_tools", "tests/fixtures"):
        for path in sorted((source / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                project[path.relative_to(source).as_posix()] = path.read_bytes()
    binaries = ensure_windows_launchers(source)
    variants = {(component, platform): component_entries(component, source, platform)
                for component in ("Agent", "Manager") for platform in PLATFORMS}
    for name in ("snmp_agent_config.json", "snmp_manager_config.json"):
        project[name] = variants[("Agent" if name.startswith("snmp_agent") else "Manager", "Linux")]["config.json"]
    project["VERSION"] = (__version__ + "\n").encode("ascii")
    packages = []
    validation_bytes = (json.dumps(validation, indent=2) + "\n").encode("utf-8")
    # Each release archive contains both components for one platform only; legacy aliases are no longer generated.
    for platform in PLATFORMS:
        bundle = {}
        for component in ("Agent", "Manager"):
            entries = with_manifest(variants[(component, platform)], component, platform)
            bundle.update({component + "/" + name: data for name, data in entries.items()})
        platform_project = dict(project)
        if platform == "Windows":
            for path in (*binaries.values(), source / "build" / "windows" / "BUILD.json"):
                platform_project[path.relative_to(source).as_posix()] = path.read_bytes()
        bundle["README.md"] = render_document("README-Release.md", "Release", platform, source)
        bundle["HELP.md"] = render_document("HELP-Release.md", "Release", platform, source)
        bundle[METADATA_DIRECTORY + "/VALIDATION.json"] = validation_bytes
        bundle[METADATA_DIRECTORY + "/VERSION"] = project["VERSION"]
        bundle.update({".hookah/source/" + name: data for name, data in platform_project.items()})
        name = f"Hookah-{platform}-{__version__}"
        packages.append(write_archive(output_directory / (name + ".zip"), bundle, "Release", prefix=name + "/", platform=platform))
    (output_directory / "VALIDATION.json").write_bytes(validation_bytes)
    checksums = "".join(f"{sha256(package.read_bytes())}  {package.name}\n" for package in packages)
    (output_directory / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    return packages


def run_verification(source=ROOT, python=sys.executable):
    source = Path(source).resolve()
    ensure_windows_launchers(source)
    ensure_bundles(source)
    reports = {"version": __version__, "native_windows_execution": sys.platform == "win32",
               "windows_launcher_runner": "native" if os.name == "nt" else
                   "wine" if os.environ.get("HOOKAH_WINE") or shutil.which("wine") or shutil.which("wine64") else "unavailable",
               "bundled_runtime": True}
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
        print(f"Release {__version__} verified; exactly two OS ZIPs, SHA256SUMS, and VALIDATION.json written.")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
