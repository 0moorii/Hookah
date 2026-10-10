"""Validated, portable defaults for the Hookah Manager CLI."""

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


CONFIG_NAME = "snmp_manager_config.json"
ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ManagerConfig:
    target: str | None = None
    version: str = "2c"
    community: str = "public"
    interval: float = 1.0
    timeout: float = 2.0
    retries: int = 1
    counter_bits: str = "auto"
    snmp_bin: Path | None = None
    encryption_config: Path | None = None

    def __post_init__(self):
        if self.target is not None and (
            not isinstance(self.target, str) or not self.target.strip() or self.target.startswith("-")
        ):
            raise ValueError("target must be a nonempty hostname or udp:host:port, or null.")
        if self.version not in ("1", "2c"):
            raise ValueError("version must be '1' or '2c'.")
        if not isinstance(self.community, str) or not self.community:
            raise ValueError("community must be a nonempty string.")
        for name in ("interval", "timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number in seconds.")
        if type(self.retries) is not int or not 0 <= self.retries <= 10:
            raise ValueError("retries must be an integer between 0 and 10.")
        if self.counter_bits not in ("auto", "32", "64"):
            raise ValueError("counter_bits must be 'auto', '32', or '64'.")
        for name in ("snmp_bin", "encryption_config"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                raise ValueError(f"{name} must be a path or null.")

    def document(self):
        return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self).items()}


def config_from_document(document, directory):
    if not isinstance(document, dict):
        raise ValueError("The manager configuration must be a JSON object.")
    unknown = set(document) - {item.name for item in fields(ManagerConfig)}
    if unknown:
        raise ValueError("Unknown manager configuration keys: " + ", ".join(sorted(unknown)))
    settings = dict(document)
    for name in ("snmp_bin", "encryption_config"):
        value = settings.get(name)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty path string or null.")
            path = Path(value).expanduser()
            settings[name] = (path if path.is_absolute() else Path(directory) / path).resolve()
    return ManagerConfig(**settings)


def default_config_path():
    """An explicit environment path wins over working-directory/package defaults."""
    configured = os.environ.get("HOOKAH_MANAGER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    local = Path(CONFIG_NAME)
    if local.is_file():
        return local
    bundled = ROOT / CONFIG_NAME
    return bundled if bundled.is_file() else None


def load_manager_config(path=None, *, use_defaults=True):
    selected = Path(path).expanduser() if path is not None else default_config_path() if use_defaults else None
    if selected is None:
        return ManagerConfig()
    with selected.open(encoding="utf-8-sig") as source:
        return config_from_document(json.load(source), selected.resolve().parent)


def initialize_manager_config(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = ManagerConfig(target="udp:127.0.0.1:1161").document()
    # Exclusive creation preserves an existing profile, including under races.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        json.dump(document, destination, indent=2, allow_nan=False)
        destination.write("\n")
    return document
