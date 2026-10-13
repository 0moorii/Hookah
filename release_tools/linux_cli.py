#!/usr/bin/env python3
"""Parameter-only entrypoints for the packaged Linux Agent and Manager."""

import argparse
import hashlib
import importlib
import importlib.util
import ipaddress
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_AGENT_PORT = 1161
PROFILE_OPTIONS = {"config", "no_config", "banner", "snmp_bin"}
CONNECTION_OPTIONS = {"target", "port", "community", "snmp_version", "timeout", "retries", "encryption_config"}
MANAGER_SETTINGS = {"target", "community", "snmp_version", "timeout", "retries", "interval",
                    "counter_bits", "snmp_bin", "encryption_config"}
AGENT_START_OPTIONS = {"listen_address", "port", "manager", "sample_interval", "state_file", "log_level"}
AGENT_REMOTE_OPTIONS = {"remote", "community", "snmp_version", "timeout", "encryption_config"}
PAYLOAD_ACTIONS = {"add_agents": "add_agent", "select_agent": "select_agent", "remove_agent": "remove_agent",
                   "plot": "plot", "compare": "compare", "add_managers": "add_manager"}


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Use a finite positive number of seconds.")
    return number


def udp_port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("UDP ports must be between 1 and 65535.")
    return number


def retry_count(value):
    number = int(value)
    if not 0 <= number <= 10:
        raise argparse.ArgumentTypeError("Use between 0 and 10 retries.")
    return number


def option(parser, *flags, **settings):
    settings.setdefault("default", None)
    return parser.add_argument(*flags, **settings)


def build_parser(component, version):
    launcher = "hookah-ag" if component == "Agent" else "hookah-mg"
    parser = argparse.ArgumentParser(
        prog=launcher, description=f"Hookah {component} for Linux: commands use parameters, never numbered menus.",
        epilog=("No parameters starts the foreground Agent. Press Ctrl+C to stop."
                if component == "Agent" else
                "No parameters prints help. Add agents with -a ADDRESS; choose one with --select-agent ADDRESS."),
    )
    parser.add_argument("-v", "--version", action="version", version=f"Hookah {component} {version}")
    option(parser, "--config", type=Path, help="Use another JSON profile instead of the adjacent config.json")
    actions = parser.add_argument_group("operations").add_mutually_exclusive_group()
    common = {"setup": "Verify the included offline runtime", "install-cli": "Add this command to your user PATH directory",
              "config-init": "Create a profile without overwriting an existing file", "config-show": "Show the current profile",
              "config-set": "Validate and save the supplied configuration parameters", "mib": "Display the named MIB tree"}
    operations = dict(common)
    if component == "Manager":
        operations.update({"agents": "List saved agents and mark the active target", "get": "Read one OID",
                           "walk": "Read every OID beneath a subtree", "set": "Write an authorized OID",
                           "capture": "Capture samples and counter rates to CSV", "catalog": "List available OIDs",
                           "browse": "Read named metrics and readable units", "doctor": "Check runtime dependencies",
                           "generate-keys": "Generate RSA keys and an encryption profile"})
        option(actions, "-a", "--add-agent", dest="add_agents", action="append", metavar="ADDRESS",
               help="Save an agent IP/hostname[:port]; repeat to add several (default UDP 1161)")
        option(actions, "--select-agent", metavar="ADDRESS", help="Make a saved agent the default target")
        option(actions, "--remove-agent", metavar="ADDRESS", help="Remove a saved agent")
        option(actions, "--plot", type=Path, metavar="CSV", help="Plot a captured CSV without prompts")
        option(actions, "--compare", nargs=2, type=Path, metavar=("FIRST_CSV", "SECOND_CSV"),
               help="Overlay two compatible captures without prompts")
    else:
        operations.update({"start": "Start the foreground SNMP Agent", "list-oids": "Collect and show local OID values"})
        option(actions, "-a", "--add-manager", dest="add_managers", action="append", metavar="IP_OR_CIDR",
               help="Save an allowed Manager address without removing existing permissions")
    for name, description in operations.items():
        option(actions, "--" + name, dest="operation", action="store_const", const=name.replace("-", "_"), help=description)
    option(parser, "--bin-dir", type=Path, help="Directory for --install-cli (default: ~/.local/bin)")
    option(parser, "--group", help="MIB or browse collection family, such as cpu, memory, storage, or control")
    option(parser, "--root", help="Numeric MIB subtree root")
    option(parser, "--values", action="store_true", help="Include live values with --mib")
    if component == "Manager":
        connection = parser.add_argument_group("connection and saved defaults")
        option(connection, "--target", help="Use an IP/hostname[:port] for this command instead of the active agent")
        option(connection, "--port", type=udp_port, help="Override the target port or the port for newly added agents")
        option(connection, "--community", help="SNMP read or write community")
        option(connection, "--snmp-version", choices=("1", "2c"), help="SNMP protocol version; -v always prints the application version")
        option(connection, "--timeout", type=positive_seconds, help="Request timeout in seconds")
        option(connection, "--retries", type=retry_count, help="Request retry count, from 0 through 10")
        option(connection, "--snmp-bin", type=Path, help="Use external Net-SNMP tools from this directory")
        option(connection, "--no-config", action="store_true", help="Ignore profile defaults for this command")
        encryption = connection.add_mutually_exclusive_group()
        option(encryption, "--encryption-config", type=Path, help="RSA encryption profile; use --config-set to save it")
        option(encryption, "--disable-encryption", action="store_true", help="Remove the saved encryption profile with --config-set")
        banner = connection.add_mutually_exclusive_group()
        option(banner, "--banner", action="store_true", help="Print the startup banner before the command")
        option(banner, "--no-banner", dest="banner", action="store_false", help="Suppress the startup banner")
        query = parser.add_argument_group("query and capture parameters")
        option(query, "--oid", help="Numeric instance OID for GET/SET/capture, or subtree root for WALK")
        option(query, "--type", dest="data_type", help="SNMP SET type, such as s for text or i for an integer")
        option(query, "--value", help="Value for --set")
        option(query, "--output", type=Path, help="CSV or PNG destination; required for --capture")
        option(query, "--quiet", action="store_true", help="Save query results to --output without terminal output")
        option(query, "--interval", type=positive_seconds, help="Polling interval in seconds")
        option(query, "--duration", type=positive_seconds, help="Capture duration in seconds; omitted means until Ctrl+C")
        option(query, "--counter-bits", choices=("auto", "32", "64"), help="Counter-width fallback")
        option(query, "--sync-agent", action="store_true", help="Follow the Agent's current sampling interval")
        plot = parser.add_argument_group("plot and comparison parameters")
        display = plot.add_mutually_exclusive_group()
        option(display, "--show", action="store_true", help="Also show the graph in a graphical window")
        option(display, "--no-show", dest="show", action="store_false", help="Save the graph without a window (the default)")
        option(plot, "--color", choices=("blue", "red", "yellow", "green", "hotpink", "black"), help="Plot line color")
        option(plot, "--scale", choices=("auto", "linear", "symlog"), help="Y-axis scaling")
        option(plot, "--memory-mode", choices=("used", "percent", "both"), help="Physical-memory display mode")
        option(plot, "--label-first", help="First capture legend label")
        option(plot, "--label-second", help="Second capture legend label")
        option(plot, "--title", help="Comparison graph title")
        option(parser, "--directory", type=Path, help="RSA key output directory (default: keys)")
        option(parser, "--bits", type=int, choices=(2048, 3072, 4096), help="RSA key size (default: 3072)")
    else:
        settings = parser.add_argument_group("Agent settings and configuration changes")
        option(settings, "--listen-address", help="Local IPv4 bind address")
        option(settings, "--port", type=udp_port, help="Local UDP port (package default: 1161)")
        option(settings, "--manager", action="append", metavar="IP_OR_CIDR", help="Allowed Manager; repeat for more than one")
        option(settings, "--sample-interval", type=positive_seconds, help="Default sampling interval in seconds")
        option(settings, "--state-file", type=Path, help="Writable system-value persistence file")
        option(settings, "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity")
        option(settings, "--interval", nargs=2, action="append", metavar=("METRIC", "SECONDS"), help="Save a collection interval with --config-set")
        option(settings, "--clear-interval", action="append", metavar="METRIC", help="Remove a saved collection interval")
        option(settings, "--set", dest="assignment", nargs=2, action="append", metavar=("FIELD", "VALUE"), help="Save a profile field; JSON arrays/objects are accepted")
        option(settings, "--encryption", choices=("on", "off"), help="Save whether Agent traffic encryption is enabled")
        option(settings, "--rsa-private-key", type=Path, help="Private PEM for incoming encrypted traffic")
        option(settings, "--rsa-peer-public-key", type=Path, help="Trusted peer public PEM")
        remote = parser.add_argument_group("remote Agent configuration")
        option(remote, "--remote", help="Remote Agent IP/hostname[:port] for --config-show or --config-set")
        option(remote, "--community", help="Remote read community, or write community for changes")
        option(remote, "--snmp-version", choices=("1", "2c"), help="Remote SNMP protocol version")
        option(remote, "--timeout", type=positive_seconds, help="Remote request timeout in seconds")
        option(remote, "--encryption-config", type=Path, help="Encrypted remote transport profile")
    return parser


def check_options(parser, args, allowed):
    excluded = {"operation", *PAYLOAD_ACTIONS}
    unexpected = {name for name, value in vars(args).items() if value is not None} - allowed - excluded
    if unexpected:
        parser.error("These parameters do not apply to this operation: " + ", ".join("--" + name.replace("_", "-") for name in sorted(unexpected)))


def parameters(args, names, aliases=None):
    result = []
    aliases = {"snmp_version": "version", "data_type": "type", "assignment": "set", **(aliases or {})}
    for name in names:
        value = getattr(args, name, None)
        if value is None:
            continue
        flag = "--" + aliases.get(name, name.replace("_", "-"))
        if name in ("banner", "show"):
            result.append(flag if value else "--no-" + name)
        elif value is True:
            result.append(flag)
        elif isinstance(value, list):
            for item in value:
                result.extend([flag, *(str(part) for part in item)] if isinstance(item, list) else [flag + "=" + str(item)])
        else:
            result.append(flag + "=" + str(value.expanduser().resolve() if isinstance(value, Path) else value))
    return result


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            os.fchmod(destination.fileno(), mode)
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, document):
    data = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    atomic_write(path, data.encode("utf-8"))


def endpoint(value, port=None, default_port=DEFAULT_AGENT_PORT):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Use an Agent IPv4 address or hostname, optionally with :port.")
    address = value.removeprefix("udp:")
    pieces = address.split(":")
    if len(pieces) > 2 or not pieces[0] or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", pieces[0]):
        raise ValueError("Use an Agent IPv4 address or hostname, optionally with :port.")
    host = pieces[0].lower()
    if re.fullmatch(r"[0-9.]+", host):
        host = str(ipaddress.IPv4Address(host))
    elif any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split(".")):
        raise ValueError("Use a valid Agent hostname.")
    if len(pieces) == 2:
        selected = udp_port(pieces[1]) if port is None else port
    else:
        selected = port if port is not None else default_port
    return f"udp:{host}:{selected}"


def app_module(root, name):
    directory = str(root / ".hookah/app")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


def manager_document(root, profile):
    with profile.open(encoding="utf-8-sig") as source:
        document = json.load(source)
    app_module(root, "snmp_manager_config").config_from_document(document, profile.parent)
    return document


def registry_path(root, profile):
    suffix = "" if profile == root / "config.json" else "-" + hashlib.sha256(str(profile).encode()).hexdigest()[:16]
    return root / ".hookah/config" / f"agents{suffix}.json"


def saved_agents(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict) or set(document) != {"agents"} or not isinstance(document["agents"], list):
        raise ValueError("The saved Agent list must contain an agents array.")
    return list(dict.fromkeys(endpoint(address) for address in document["agents"]))


def matching_agent(address, agents, port=None):
    selected = endpoint(address, port)
    if ":" not in address.removeprefix("udp:") and port is None:
        host = selected.rsplit(":", 1)[0]
        matches = [item for item in agents if item.rsplit(":", 1)[0] == host]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("This agent has several saved ports. Include :port in the address.")
    elif selected in agents:
        return selected
    raise ValueError("Agent is not saved. Add it first with hookah-mg -a ADDRESS.")


def manage_agents(root, profile, operation, args):
    document = manager_document(root, profile)
    original_target = document.get("target")
    path = registry_path(root, profile)
    agents = saved_agents(path)
    active = endpoint(document["target"], default_port=161) if document.get("target") else None
    if operation == "agents":
        print("Saved agents (* = active):")
        for address in agents:
            print(("* " if address == active else "  ") + address)
        if not agents:
            print("No agents saved. Use hookah-mg -a ADDRESS to add one.")
        return 0
    previous_agents = list(agents)
    if operation == "add_agent":
        for address in args.add_agents:
            selected = endpoint(address, args.port)
            if selected not in agents:
                agents.append(selected)
        if not previous_agents:
            document["target"] = agents[0]
    elif operation == "select_agent":
        document["target"] = matching_agent(args.select_agent, agents, args.port)
    else:
        selected = matching_agent(args.remove_agent, agents, args.port)
        agents.remove(selected)
        if selected == active:
            document["target"] = agents[0] if agents else None
    app_module(root, "snmp_manager_config").config_from_document(document, profile.parent)
    # Save the visible profile first so a failed profile write cannot change the list.
    original = profile.read_bytes()
    changed_target = document.get("target") != original_target
    if changed_target:
        atomic_json(profile, document)
    try:
        if agents != previous_agents:
            atomic_json(path, {"agents": agents})
    except OSError:
        if changed_target:
            atomic_write(profile, original)
        raise
    print("Active agent: " + str(document.get("target") or "none"))
    print(f"Saved agents: {len(agents)}")
    return 0


def save_manager_settings(root, profile, args):
    document = manager_document(root, profile)
    updates = {name: getattr(args, name) for name in MANAGER_SETTINGS if getattr(args, name) is not None}
    if args.disable_encryption:
        updates["encryption_config"] = None
    if not updates:
        raise ValueError("Supply at least one setting with --config-set.")
    if "snmp_version" in updates:
        updates["version"] = updates.pop("snmp_version")
    if "target" in updates:
        updates["target"] = endpoint(updates["target"], args.port)
    if "encryption_config" in updates and updates["encryption_config"] is not None:
        crypto = app_module(root, "snmp_crypto")
        crypto.RsaEnvelope.from_settings(crypto.load_encryption_settings(updates["encryption_config"].expanduser().resolve()))
    updates = {name: str(value.expanduser().resolve()) if isinstance(value, Path) else value for name, value in updates.items()}
    document.update(updates)
    app_module(root, "snmp_manager_config").config_from_document(document, profile.parent)
    atomic_json(profile, document)
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


def manager_arguments(parser, root, profile, operation, args):
    extras = {
        "get": {"oid", "output", "quiet"}, "walk": {"oid", "output", "quiet"},
        "set": {"oid", "output", "quiet", "data_type", "value"},
        "capture": {"oid", "output", "quiet", "interval", "duration", "counter_bits", "sync_agent"},
        "browse": {"group", "root", "output", "quiet"}, "mib": {"group", "root", "output", "quiet", "values"},
        "plot": {"output", "show", "color", "scale", "memory_mode"},
        "compare": {"output", "show", "scale", "memory_mode", "label_first", "label_second", "title"},
        "generate_keys": {"directory", "bits"}, "catalog": set(), "doctor": set(),
        "config_show": set(), "config_init": set(),
    }
    network = operation in ("get", "walk", "set", "capture", "browse", "mib")
    allowed = PROFILE_OPTIONS | extras[operation] | (CONNECTION_OPTIONS if network else set())
    check_options(parser, args, allowed)
    if args.quiet and args.output is None:
        parser.error("--quiet requires --output so query results are not lost.")
    if operation in ("get", "walk", "set", "capture") and args.oid is None:
        parser.error("--" + operation + " requires --oid.")
    if operation == "set" and (args.data_type is None or args.value is None):
        parser.error("--set requires --type and --value.")
    if operation == "capture" and args.output is None:
        parser.error("--capture requires --output.")
    if args.target is not None:
        args.target = endpoint(args.target, args.port)
    elif args.port is not None or (operation == "mib" and (args.values or args.output)):
        document = {} if args.no_config else manager_document(root, profile)
        if not document.get("target"):
            parser.error("Supply --target or select a saved agent first.")
        args.target = endpoint(document["target"], args.port)
    result = ["--no-config"] if args.no_config else ["--config", str(profile)]
    result.extend(parameters(args, ("banner", "snmp_bin")))
    if operation in ("config_show", "config_init"):
        return result + ["config", operation.split("_")[1]]
    if operation == "generate_keys":
        return result + ["keys", "generate"] + parameters(args, ("directory", "bits"))
    result.append(operation)
    if operation == "plot":
        result.append(str(args.plot.expanduser().resolve()))
    elif operation == "compare":
        result.extend(str(path.expanduser().resolve()) for path in args.compare)
    result.extend(parameters(args, ("target", "community", "snmp_version", "timeout", "retries", "encryption_config",
                                   "oid", "output", "data_type", "value", "interval", "duration", "counter_bits",
                                   "sync_agent", "group", "root", "show", "color", "scale", "memory_mode",
                                   "label_first", "label_second", "title")))
    return result


def agent_arguments(parser, profile, operation, args):
    base = {"config", "log_level"}
    changes = {"interval", "clear_interval", "assignment", "encryption", "rsa_private_key", "rsa_peer_public_key"}
    allowed = {
        "start": base | AGENT_START_OPTIONS,
        "list_oids": base | AGENT_START_OPTIONS,
        "mib": base | AGENT_START_OPTIONS | {"group", "root", "values"},
        "config_init": base,
        "config_show": base | AGENT_REMOTE_OPTIONS,
        "config_set": base | AGENT_START_OPTIONS | AGENT_REMOTE_OPTIONS | changes,
    }[operation]
    check_options(parser, args, allowed)
    result = ["--config", str(profile)] + parameters(args, ("listen_address", "port", "manager", "sample_interval", "state_file", "log_level"))
    if operation.startswith("config_"):
        result.extend(["config", operation.split("_")[1]])
        result.extend(parameters(args, ("remote", "community", "snmp_version", "timeout", "encryption_config",
                                       "interval", "clear_interval", "assignment", "encryption", "rsa_private_key", "rsa_peer_public_key")))
    elif operation == "list_oids":
        result.append("--list-oids")
    elif operation == "mib":
        result.append("mib")
        result.extend(parameters(args, ("group", "root", "values")))
    return result


def load_runtime(root):
    path = root / ".hookah/app/hookah_runtime.py"
    specification = importlib.util.spec_from_file_location("hookah_package_runtime", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def install_cli(root, launcher, directory):
    directory = (directory or Path.home() / ".local/bin").expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / launcher
    source = root / launcher
    if destination.is_symlink() and destination.resolve() == source:
        print(f"Already installed: {destination}")
        return 0
    destination.symlink_to(source)
    print(f"Installed: {destination}")
    print(f"Add {directory} to PATH if it is not already included.")
    return 0


def main(argv=None, root=None):
    root = Path(root or os.environ.get("HOOKAH_PACKAGE_ROOT") or Path(__file__).resolve().parents[1]).resolve()
    launcher = "hookah"
    try:
        runtime = load_runtime(root)
        metadata = runtime.package_metadata(root)
        if metadata.get("platform") != "Linux":
            raise ValueError("These parameter entrypoints are available only in Linux packages.")
        component = metadata["component"]
        launcher = "hookah-ag" if component == "Agent" else "hookah-mg"
        parser = build_parser(component, metadata["version"])
        args = parser.parse_args(argv)
        operation = args.operation or next((action for name, action in PAYLOAD_ACTIONS.items() if getattr(args, name, None) is not None), None)
        if operation is None:
            if component == "Manager":
                parser.print_help()
                return 0
            operation = "start"
        configured = os.environ.get("HOOKAH_MANAGER_CONFIG") if component == "Manager" else None
        profile = Path(args.config or configured or root / "config.json").expanduser().resolve()
        if operation == "install_cli":
            check_options(parser, args, {"bin_dir"})
            return install_cli(root, launcher, args.bin_dir)
        if operation == "setup":
            check_options(parser, args, {"snmp_bin"} if component == "Manager" else set())
            return runtime.main(["setup", *parameters(args, ("snmp_bin",))], root)
        if operation in ("agents", "add_agent", "select_agent", "remove_agent"):
            check_options(parser, args, {"config"} | ({"port"} if operation != "agents" else set()))
            return manage_agents(root, profile, operation, args)
        if operation == "add_manager":
            check_options(parser, args, {"config"})
            agent = app_module(root, "snmp_agent")
            document = agent.read_config_document(profile)
            current = list(agent.config_from_document(document, profile.parent).allowed_managers)
            for address in args.add_managers:
                network = str(ipaddress.IPv4Network(address, strict=False))
                if network not in current:
                    current.append(network)
            agent.update_config_file(profile, {"allowed_managers": current})
            print(json.dumps(agent.read_config_document(profile), indent=2))
            return 0
        if operation == "config_set" and component == "Manager":
            check_options(parser, args, {"config", "disable_encryption", "port"} | MANAGER_SETTINGS)
            if args.port is not None and args.target is None:
                parser.error("Use --target with --port when saving connection defaults.")
            if not args.disable_encryption and not any(getattr(args, name) is not None for name in MANAGER_SETTINGS):
                parser.error("Supply at least one setting with --config-set.")
            return save_manager_settings(root, profile, args)
        translated = manager_arguments(parser, root, profile, operation, args) if component == "Manager" else agent_arguments(parser, profile, operation, args)
        if component == "Manager" and args.quiet:
            environment = dict(os.environ, HOOKAH_PACKAGE_ROOT=str(root), HOOKAH_PROGRAM_NAME=launcher)
            result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-B", str(root / ".hookah/app/hookah_runtime.py"), *translated],
                                    env=environment, stdout=subprocess.DEVNULL)
            return result.returncode
        return runtime.main(translated, root)
    except (OSError, ValueError, RuntimeError, ImportError, argparse.ArgumentTypeError) as exc:
        print(f"{launcher}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
