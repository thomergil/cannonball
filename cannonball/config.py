"""Configuration: defaults.toml, overridden key by key by the user's file."""

import ipaddress
import os
import plistlib
import socket
import sys
import tomllib
import typing
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

import ifaddr

from . import consts

DEFAULTS = Path(__file__).with_name("defaults.toml")
DEFAULT_PATH = Path(consts.CONFIG_PATH).expanduser()


class ConfigError(SystemExit):
    pass


@dataclass(frozen=True)
class Download:
    destination: Path
    subfolder: str
    chunk_size_mb: float

    @property
    def chunk_size(self) -> int:
        return int(self.chunk_size_mb * consts.MIB)


@dataclass(frozen=True)
class Timing:
    poll_interval: float
    announce_interval: float
    connect_window: float
    connect_timeout: float
    connect_retry: float
    op_timeout: float
    event_poll: float
    idle_timeout: float
    stall_timeout: float
    retry_delay: float
    resync_after: float
    ping_timeout: float


@dataclass(frozen=True)
class Networks:
    """The networks Cannonball works on."""

    nets: tuple[ipaddress.IPv4Network, ...]

    def __contains__(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in net for net in self.nets)

    def interfaces(self) -> dict[int, str]:
        """This computer's interfaces on these networks: index -> IPv4 address."""
        return {a.index: ip.ip for a in ifaddr.get_adapters() for ip in a.ips if isinstance(ip.ip, str) and ip.ip in self}


@dataclass
class Config:
    download: Download
    hosts: list[str]
    networks: Networks
    name: str
    guid: uuid.UUID
    advertise: bool
    port: int
    announce_port: int
    state_dir: Path
    log_file: Path | None
    log_level: str
    timing: Timing
    ping_misses: int
    path: Path | None = None  # the user's file, if there is one


def _path(value) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def _itu2_guid() -> uuid.UUID | None:
    """The GUID Canon Image Transfer Utility 2 paired the camera with, if any."""
    try:
        with _path(consts.ITU2_PREFS).open("rb") as f:
            return uuid.UUID(bytes=bytes(plistlib.load(f)[consts.ITU2_GUID_KEY]))
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return None


def _stored_guid(state_dir: Path) -> uuid.UUID:
    """A GUID of our own, created on first run and kept in state_dir."""
    path = state_dir / consts.STATE_GUID
    try:
        return uuid.UUID(path.read_text().strip())
    except (OSError, ValueError):
        guid = uuid.uuid4()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{guid}\n")
        return guid


def _merge(base: dict, override: dict, where: str = "") -> dict:
    out = dict(base)
    for key, value in override.items():
        if key not in base:
            raise ConfigError(f"unknown setting {where}{key}")
        out[key] = _merge(base[key], value or {}, f"{where}{key}.") if isinstance(base[key], dict) else value
    return out


def _section(cls, raw: dict):
    """Build a dataclass from a dict, converting each value to its field's type."""
    hints = typing.get_type_hints(cls)
    try:
        return cls(**{f.name: hints[f.name](raw[f.name]) for f in fields(cls)})
    except (TypeError, ValueError) as e:
        raise ConfigError(f"bad setting in {cls.__name__.lower()}: {e}") from e


def _log_file(value: str, state_dir: Path) -> Path | None:
    if not value:
        return None
    if value == consts.LOG_FILE_AUTO:
        folder = _path(consts.MACOS_LOG_DIR) if sys.platform == "darwin" else state_dir
        return folder / consts.LOG_NAME
    return _path(value)


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    raw = tomllib.loads(DEFAULTS.read_text())
    user = {}
    if path.exists():
        try:
            user = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    raw = _merge(raw, user)

    state_dir = _path(raw["state_dir"])
    try:
        networks = Networks(tuple(ipaddress.IPv4Network(n) for n in raw["networks"]))
        guid = uuid.UUID(str(raw["guid"])) if raw["guid"] else _itu2_guid() or _stored_guid(state_dir)
    except ValueError as e:
        raise ConfigError(f"bad setting: {e}") from e
    return Config(
        download=_section(Download, {**raw["download"], "destination": _path(raw["download"]["destination"])}),
        hosts=[str(h) for h in raw["hosts"]],
        networks=networks,
        name=str(raw["name"]) or socket.gethostname().split(".")[0],
        guid=guid,
        advertise=bool(raw["advertise"]),
        port=int(raw["port"]),
        announce_port=int(raw["announce_port"]),
        state_dir=state_dir,
        log_file=_log_file(str(raw["log_file"] or ""), state_dir),
        log_level=str(raw["log_level"]).upper(),
        timing=_section(Timing, raw["timing"]),
        ping_misses=int(raw["ping_misses"]),
        path=path if path.exists() else None,
    )
