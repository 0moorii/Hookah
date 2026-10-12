"""Build portable, already-installed Python runtimes for offline distributions."""

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("Agent", "Manager")
PLATFORMS = ("Linux", "Windows")


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_fingerprint(component, platform, source=ROOT):
    digest = hashlib.sha256((component + platform).encode("ascii"))
    for name in ("release_tools/bundles.py", "release_tools/bundle-lock.json",
                 f"release_tools/requirements-bundle-{component}.txt"):
        digest.update((Path(source) / name).read_bytes())
    return digest.hexdigest()


def download_runtime(platform, source=ROOT):
    source = Path(source)
    lock = json.loads((source / "release_tools/bundle-lock.json").read_text(encoding="utf-8"))
    runtime = lock["runtimes"][platform]
    directory = source / "build" / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / runtime["asset"]
    if archive.is_file() and file_digest(archive) == runtime["sha256"]:
        return archive
    temporary = archive.with_suffix(archive.suffix + ".partial")
    try:
        request = urllib.request.Request(runtime["url"], headers={"User-Agent": "Hookah-bundle-builder"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if file_digest(temporary) != runtime["sha256"]:
            raise ValueError("The downloaded Python runtime failed its SHA-256 check.")
        temporary.replace(archive)
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def extract_runtime(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    links = []
    with tarfile.open(archive, "r:gz") as package:
        for member in package.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("Unsafe Python runtime archive member: " + member.name)
            if not name.parts or name.parts[0] != "python":
                continue
            relative = Path(*name.parts[1:])
            if not relative.parts:
                continue
            path = destination / relative
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as input_file, path.open("wb") as output:
                    shutil.copyfileobj(input_file, output)
                path.chmod(member.mode & 0o777)
            elif member.issym() or member.islnk():
                links.append((path, member.linkname, member.islnk()))
            else:
                raise ValueError("Unsupported Python runtime archive member: " + member.name)
    # Materialized links also work with ordinary Windows ZIP extractors.
    while links:
        pending = []
        for path, link, hard in links:
            target = destination / Path(*PurePosixPath(link).parts[1:]) if hard else path.parent / link
            target = target.resolve()
            if destination.resolve() not in target.parents:
                raise ValueError("A Python runtime link escapes the runtime directory.")
            if not target.is_file():
                pending.append((path, link, hard))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, path)
        if len(pending) == len(links):
            raise ValueError("The Python runtime contains an unresolved link.")
        links = pending


def download_wheels(component, platform, source=ROOT):
    source = Path(source)
    requirements = source / "release_tools" / f"requirements-bundle-{component}.txt"
    key = hashlib.sha256(requirements.read_bytes() + platform.encode() + b"pywin32==312").hexdigest()[:16]
    directory = source / "build" / "downloads" / f"wheels-{component}-{platform}-{key}"
    record = directory / "WHEELS.json"
    if record.is_file():
        hashes = json.loads(record.read_text(encoding="utf-8"))
        if all((directory / name).is_file() and file_digest(directory / name) == digest for name, digest in hashes.items()):
            return [directory / name for name in sorted(hashes)]
    directory.mkdir(parents=True, exist_ok=True)
    arguments = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--python-version", "312",
                 "--implementation", "cp", "--abi", "cp312", "--dest", str(directory), "-r", str(requirements)]
    targets = ("win_amd64",) if platform == "Windows" else (
        "manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64",
    )
    for target in targets:
        arguments.extend(("--platform", target))
    if component == "Agent" and platform == "Windows":
        arguments.append("pywin32==312")
    subprocess.run(arguments, check=True, timeout=300)
    wheels = sorted(directory.glob("*.whl"))
    hashes = {path.name: file_digest(path) for path in wheels}
    record.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    return wheels


def install_wheels(wheels, environment, platform):
    environment = Path(environment)
    site = environment / ("Lib/site-packages" if platform == "Windows" else "lib/python3.12/site-packages")
    site.mkdir(parents=True, exist_ok=True)
    distributions = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as package:
            for info in package.infolist():
                name = PurePosixPath(info.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsafe wheel member: " + info.filename)
                if info.is_dir():
                    continue
                parts = list(name.parts)
                if parts and parts[0].endswith(".data"):
                    if len(parts) < 3:
                        continue
                    if parts[1] in ("purelib", "platlib"):
                        parts = parts[2:]
                    elif parts[1] == "data":
                        target = environment / Path(*parts[2:])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(package.read(info))
                        continue
                    else:
                        continue
                target = site / Path(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = package.read(info)
                target.write_bytes(data)
                if info.filename.endswith(".dist-info/METADATA"):
                    metadata = BytesParser().parsebytes(data)
                    distributions[metadata["Name"]] = metadata["Version"]
    return distributions


def trim_environment(environment):
    environment = Path(environment)
    # Remove development-only data while retaining libraries, fonts, licenses, and metadata.
    for directory in sorted(environment.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir() and directory.name in ("__pycache__", "tests", "test", "idlelib", "turtledemo"):
            shutil.rmtree(directory)
        elif directory.is_file() and directory.suffix in (".pyc", ".pyo"):
            directory.unlink()
    if (environment / "bin/python").is_file():
        # Keep a single portable executable; copied aliases otherwise triple the ZIP size.
        for path in (environment / "bin").iterdir():
            if path.name != "python":
                path.unlink() if path.is_file() else shutil.rmtree(path)
        for name in ("lib/libpython3.12.so", "lib/pkgconfig", "include", "share/man"):
            path = environment / name
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    else:
        for name in ("include", "libs"):
            path = environment / name
            if path.is_dir():
                shutil.rmtree(path)


def verify_bundle(directory, component, platform, source=ROOT):
    directory = Path(directory)
    metadata = json.loads((directory / "BUNDLE.json").read_text(encoding="utf-8"))
    if metadata.get("fingerprint") != bundle_fingerprint(component, platform, source):
        raise ValueError("The bundled runtime is stale.")
    for name, digest in metadata["files"].items():
        path = directory / "environment" / name
        if not path.is_file() or file_digest(path) != digest:
            raise ValueError("Bundled runtime integrity check failed: " + name)
    return metadata


def ensure_bundle(component, platform, source=ROOT):
    if component not in COMPONENTS or platform not in PLATFORMS:
        raise ValueError("Choose an Agent/Manager component and Linux/Windows platform.")
    source = Path(source).resolve()
    root = source / "build" / "bundles" / platform
    directory = root / component
    if (directory / "BUNDLE.json").is_file():
        try:
            verify_bundle(directory, component, platform, source)
            return directory
        except (OSError, ValueError, KeyError):
            pass
    archive = download_runtime(platform, source)
    wheels = download_wheels(component, platform, source)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-", dir=root) as temporary:
        stage = Path(temporary) / component
        environment = stage / "environment"
        extract_runtime(archive, environment)
        versions = install_wheels(wheels, environment, platform)
        trim_environment(environment)
        python = "python.exe" if platform == "Windows" else "bin/python"
        if not (environment / python).is_file():
            raise ValueError("The bundled Python executable is missing.")
        files = {path.relative_to(environment).as_posix(): file_digest(path)
                 for path in sorted(environment.rglob("*")) if path.is_file()}
        executables = [path.relative_to(environment).as_posix() for path in sorted(environment.rglob("*"))
                       if path.is_file() and path.stat().st_mode & stat.S_IXUSR]
        lock = json.loads((source / "release_tools/bundle-lock.json").read_text(encoding="utf-8"))
        metadata = {"component": component, "platform": platform, "python": lock["python_version"],
                    "fingerprint": bundle_fingerprint(component, platform, source), "distributions": versions,
                    "files": files, "executables": executables,
                    "runtime_source": lock["runtimes"][platform]["url"], "runtime_sha256": file_digest(archive)}
        (stage / "BUNDLE.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        if directory.exists():
            shutil.rmtree(directory)
        stage.replace(directory)
    verify_bundle(directory, component, platform, source)
    return directory


def ensure_bundles(source=ROOT):
    return {(component, platform): ensure_bundle(component, platform, source)
            for component in COMPONENTS for platform in PLATFORMS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=COMPONENTS)
    parser.add_argument("--platform", choices=PLATFORMS)
    args = parser.parse_args(argv)
    try:
        for component in (args.component,) if args.component else COMPONENTS:
            for platform in (args.platform,) if args.platform else PLATFORMS:
                print(ensure_bundle(component, platform))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Runtime bundle build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
