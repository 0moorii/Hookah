"""Build and validate genuine, reproducible Windows x64 console launchers."""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from hookah_version import __version__


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIRECTORY = ROOT / "build" / "windows"
BUILD_INPUTS = ("release_tools/windows.py", "release_tools/launchers/windows.c", "hookah_version.py")
COMPONENTS = ("Agent", "Manager")


def inspect_pe(data):
    """Reject renamed text/ELF files, non-x64 binaries, and GUI-only launchers."""
    if len(data) < 256 or data[:2] != b"MZ":
        raise ValueError("The Windows launcher must be a real PE executable.")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 24 + 112 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("The Windows launcher has an invalid PE header.")
    machine, sections, timestamp = struct.unpack_from("<HHI", data, offset + 4)
    optional_size = struct.unpack_from("<H", data, offset + 20)[0]
    optional = offset + 24
    if machine != 0x8664 or struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("The Windows launcher must target Windows x64 (PE32+).")
    subsystem = struct.unpack_from("<H", data, optional + 68)[0]
    if subsystem != 3:
        raise ValueError("The Windows launcher must use the console subsystem.")

    def file_offset(rva):
        for index in range(sections):
            section = optional + optional_size + index * 40
            if section + 40 > len(data):
                raise ValueError("The Windows launcher has a truncated section table.")
            virtual_size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
            if address <= rva < address + max(virtual_size, raw_size):
                location = raw + rva - address
                if location >= len(data):
                    break
                return location
        raise ValueError("The Windows launcher has an invalid import address.")

    imports = []
    import_rva = struct.unpack_from("<I", data, optional + 120)[0]
    if import_rva:
        descriptor = file_offset(import_rva)
        while descriptor + 20 <= len(data):
            values = struct.unpack_from("<IIIII", data, descriptor)
            if not any(values):
                break
            name = file_offset(values[3])
            end = data.find(b"\0", name, name + 256)
            if end < 0:
                raise ValueError("The Windows launcher has an invalid DLL name.")
            imports.append(data[name:end].decode("ascii"))
            descriptor += 20
    allowed = {"kernel32.dll", "msvcrt.dll", "ucrtbase.dll"}
    if not imports or any(name.lower() not in allowed for name in imports):
        raise ValueError("The launcher must depend only on standard Windows runtime DLLs.")
    return {"format": "PE32+", "architecture": "x64", "subsystem": "console", "timestamp": timestamp, "imports": imports}


def build_fingerprint(source=ROOT):
    digest = hashlib.sha256(__version__.encode("ascii"))
    for name in BUILD_INPUTS:
        digest.update(name.encode("utf-8"))
        digest.update((Path(source) / name).read_bytes())
    return digest.hexdigest()


def find_compiler(selected=None):
    selected = selected or os.environ.get("HOOKAH_WINDOWS_CC")
    if selected:
        compiler = shutil.which(selected)
    else:
        compiler = next((path for name in ("x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-gcc-posix")
                         if (path := shutil.which(name))), None)
        if compiler is None and os.name == "nt":
            compiler = shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("A Windows x64 MinGW compiler is required to build .exe launchers. "
                           "On Debian/Ubuntu install gcc-mingw-w64-x86-64, or set HOOKAH_WINDOWS_CC.")
    target = subprocess.run([compiler, "-dumpmachine"], capture_output=True, text=True, check=True).stdout.strip()
    if not target.startswith("x86_64-w64-mingw32"):
        raise RuntimeError("The launcher compiler must target x86_64-w64-mingw32, not " + target)
    return compiler


def ensure_windows_launchers(source=ROOT, output_directory=None, compiler=None, *, force=False):
    source = Path(source).resolve()
    directory = Path(output_directory) if output_directory else source / "build" / "windows"
    fingerprint = build_fingerprint(source)
    metadata_path = directory / "BUILD.json"
    outputs = {component: directory / f"Hookah-{component}.exe" for component in COMPONENTS}
    if not force and metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("fingerprint") == fingerprint and all(
                path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"][component]
                for component, path in outputs.items()
            ):
                for path in outputs.values():
                    inspect_pe(path.read_bytes())
                return outputs
        except (OSError, KeyError, ValueError):
            pass
    compiler = find_compiler(compiler)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hookah-pe-build-", dir=directory) as temporary:
        for component, output in outputs.items():
            binary = Path(temporary) / output.name
            subprocess.run([
                compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-municode", "-static", "-s",
                "-Wl,--no-insert-timestamp", "-Wl,--subsystem,console",
                f'-DHOOKAH_COMPONENT=L"{component}"', f'-DHOOKAH_VERSION=L"{__version__}"',
                str(source / "release_tools" / "launchers" / "windows.c"), "-o", str(binary),
            ], check=True, timeout=60)
            inspected = inspect_pe(binary.read_bytes())
            if inspected["timestamp"] != 0:
                raise ValueError("A reproducible Windows launcher must have a zero PE timestamp.")
            binary.replace(output)
    metadata = {"version": __version__, "fingerprint": fingerprint,
                "sha256": {component: hashlib.sha256(path.read_bytes()).hexdigest() for component, path in outputs.items()}}
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", help="Windows x64 MinGW compiler executable")
    parser.add_argument("--force", action="store_true", help="Recompile even when the verified cache is current")
    args = parser.parse_args(argv)
    try:
        for path in ensure_windows_launchers(compiler=args.compiler, force=args.force).values():
            print(path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Windows launcher build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
