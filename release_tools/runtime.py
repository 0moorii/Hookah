#!/usr/bin/env python3
"""Internal package bootstrap; end users invoke the public .sh/.exe launcher."""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def package_metadata(root):
    with (root / ".hookah" / "metadata" / "component.json").open(encoding="utf-8") as source:
        metadata = json.load(source)
    if metadata.get("component") not in ("Agent", "Manager"):
        raise ValueError("Invalid Hookah component metadata. Extract the complete ZIP again.")
    return metadata


def environment_python(root):
    environment = root / ".hookah" / "environment"
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def app_command(root, component, arguments, python=None):
    program = "snmp_agent.py" if component == "Agent" else "snmp_data_collector_v12.py"
    arguments = list(arguments)
    if component == "Agent":
        arguments = ["--config", str(root / "config.json"), *arguments]
    elif arguments[:2] == ["config", "init"] and "--config" not in arguments:
        arguments.extend(("--config", str(root / "config.json")))
    entrypoint = root / ".hookah" / "app" / program
    # Add the internal module directory explicitly, including with isolated Python runtimes.
    runner = ("import runpy,sys; from pathlib import Path; entry=sys.argv.pop(1); "
              "sys.path.insert(0,str(Path(entry).parent)); sys.argv[0]=entry; "
              "runpy.run_path(entry,run_name='__main__')")
    return [str(python or sys.executable), "-B", "-c", runner, str(entrypoint), *arguments]


def runtime_environment(root, launcher):
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                       HOOKAH_PROGRAM_NAME=launcher)
    environment.setdefault("HOOKAH_MANAGER_CONFIG", str(root / "config.json"))
    return environment


def create_environment(location):
    try:
        import venv
    except ImportError as exc:
        raise RuntimeError("Setup needs a full Python installation with venv support. "
                           "Select it using HOOKAH_PYTHON.") from exc
    venv.EnvBuilder(with_pip=True).create(location)


def setup(root, component, launcher, arguments):
    parser = argparse.ArgumentParser(prog=launcher + " setup", description="Install private runtime dependencies inside .hookah.")
    if component == "Manager":
        parser.add_argument("--snmp-bin", type=Path, help="Directory containing Net-SNMP command-line clients")
    args = parser.parse_args(arguments)
    location = root / ".hookah" / "environment"
    print("Preparing the Hookah environment...", flush=True)
    python = environment_python(root)
    if not python.is_file():
        create_environment(location)
    requirements = root / ".hookah" / "dependencies" / f"requirements-{component.lower()}.txt"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
    configuration = root / "config.json"
    if not configuration.exists():
        configuration.write_bytes((root / ".hookah" / "config" / "default.json").read_bytes())
    if component == "Manager" and args.snmp_bin:
        document = json.loads(configuration.read_text(encoding="utf-8-sig"))
        document["snmp_bin"] = str(args.snmp_bin.expanduser().resolve())
        # Use the same validator as the CLI without importing its optional dependencies.
        sys.path.insert(0, str(root / ".hookah" / "app"))
        from snmp_manager_config import config_from_document
        config_from_document(document, root)
        configuration.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    verification = ["config", "show"] if component == "Agent" else ["doctor"]
    subprocess.run(app_command(root, component, verification, python), env=runtime_environment(root, launcher), check=True)
    print(f"Setup complete. Run {launcher} to start Hookah.")
    return 0


def service(root, component, arguments):
    if component != "Agent" or os.name != "nt":
        raise ValueError("Service installation through this launcher is available for the Windows Agent.")
    if not arguments or arguments[0] not in ("install", "uninstall"):
        raise ValueError("Use service install or service uninstall. Run install in administrator PowerShell.")
    script = root / ".hookah" / "platform" / "windows" / (arguments[0] + "-agent.ps1")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments[1:]])
    return result.returncode


def main(argv=None, root=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = Path(root or os.environ.get("HOOKAH_PACKAGE_ROOT") or ROOT).resolve()
    launcher = os.environ.get("HOOKAH_PROGRAM_NAME", "Hookah")
    try:
        metadata = package_metadata(root)
        component = metadata["component"]
        if arguments == ["--version"]:
            print(f"Hookah {component} {metadata['version']}")
            return 0
        if arguments in (["--help"], ["-h"], ["help"]):
            print((root / "HELP.md").read_text(encoding="utf-8"), end="")
            return 0
        if sys.version_info < (3, 10):
            raise RuntimeError("Python 3.10+ is required. Select it using HOOKAH_PYTHON.")
        if arguments and arguments[0] == "setup":
            return setup(root, component, launcher, arguments[1:])
        if arguments and arguments[0] == "service":
            return service(root, component, arguments[1:])
        if component == "Agent":
            missing = [name for name in ("psutil", "pysnmp") if importlib.util.find_spec(name) is None]
            if missing:
                raise RuntimeError("Required dependencies are missing. Run " + launcher + " setup first.")
        command = app_command(root, component, arguments)
        environment = runtime_environment(root, launcher)
        if os.name != "nt":
            os.execve(sys.executable, command, environment)
        process = subprocess.Popen(command, env=environment)
        try:
            return process.wait()
        except KeyboardInterrupt:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait()
            return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{launcher}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
