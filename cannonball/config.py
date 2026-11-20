"""Configuration: defaults.toml, overridden key by key by the user's file."""

import ipaddress
import logging
import math
import os
import plistlib
import socket
import sys
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath

import ifaddr

from . import consts, ptpip

DEFAULTS = Path(__file__).with_name("defaults.toml")
DEFAULT_PATH = Path(consts.CONFIG_PATH).expanduser()


class ConfigError(SystemExit):
    pass


@dataclass(frozen=True)
class Download:
    destination: Path
    subfolder: str
    chunk_size_mb: float
    reserve_mb: float

    @property
    def chunk_size(self) -> int:
        return int(self.chunk_size_mb * consts.MIB)

    @property
    def reserve(self) -> int:
        """Bytes to leave free on the disk."""
        return int(self.reserve_mb * consts.MIB)

    def folder(self, when: datetime) -> Path:
        """The folder for an image taken at `when`: destination/subfolder. The
        subfolder must be a relative path without '..' (it may be a symlink).
        Raises ValueError for an unusable pattern."""
        try:
            sub = PurePath(self.subfolder.format(date=when))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"download.subfolder {self.subfolder!r} is not a valid pattern ({e})") from e
        if sub.is_absolute() or ".." in sub.parts or "\0" in str(sub):
            raise ValueError(f"download.subfolder {self.subfolder!r} must name a folder inside {self.destination}")
        return self.destination / sub


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
    """Override the defaults; each value must have its default's type (the defaults are the schema)."""
    out = dict(base)
    for key, value in override.items():
        if key not in base:
            raise ValueError(f"unknown setting {where}{key}")
        out[key] = _checked(base[key], value, f"{where}{key}")
    return out


def _checked(default, value, name: str):
    if isinstance(default, dict) and isinstance(value, dict):
        return _merge(default, value, f"{name}.")
    if isinstance(default, list) and isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    if isinstance(default, float) and isinstance(value, int | float) and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number, not {value!r}")
        return number
    if type(value) is type(default) and not isinstance(default, dict | list):
        return value
    expected = "a list of strings" if isinstance(default, list) else f"a {type(default).__name__}"
    raise ValueError(f"{name} must be {expected}, not {value!r}")


# Every timing.* value must be at least 0, and these more than 0 (at 0,
# Cannonball would spin or every operation would fail at once).
POSITIVE_TIMINGS = ("poll_interval", "event_poll", "connect_window", "connect_timeout", "connect_retry", "op_timeout", "stall_timeout")
# The worker reports progress at least this often, so each must be shorter than
# stall_timeout or the supervisor kills a healthy session.
SHORTER_THAN_STALL = ("event_poll", "connect_timeout")
MIN_CHUNK_MB = 1 / 64
MAX_NAME_BYTES = 63  # a DNS-SD instance name label (RFC 6763)

# Inclusive bounds of numeric settings, by dotted name; None is unbounded.
RANGES = {
    "port": (1, 65535),
    "announce_port": (1, 65535),
    "ping_misses": (1, None),
    "download.chunk_size_mb": (MIN_CHUNK_MB, ptpip.MAX_DATA / consts.MIB),
    "download.reserve_mb": (0, None),
    "timing.ping_timeout": (1, None),
}


def _check_ranges(raw: dict) -> None:
    for name, (low, high) in RANGES.items():
        value = raw
        for key in name.split("."):
            value = value[key]
        if value < low or (high is not None and value > high):
            limits = f"from {low:g} to {high:g}" if high is not None else f"at least {low:g}"
            raise ValueError(f"{name} must be {limits}, not {value!r}")
    for key, value in raw["timing"].items():
        if value < 0 or (value == 0 and key in POSITIVE_TIMINGS):
            raise ValueError(f"timing.{key} must be {'more than' if key in POSITIVE_TIMINGS else 'at least'} 0, not {value!r}")
    timing = raw["timing"]
    for key in SHORTER_THAN_STALL:
        if timing[key] >= timing["stall_timeout"]:
            raise ValueError(f"timing.stall_timeout must be more than timing.{key} ({timing[key]:g})")
    if len(raw["name"].encode()) > MAX_NAME_BYTES:
        raise ValueError(f"name must be at most {MAX_NAME_BYTES} bytes, not {raw['name']!r}")


def macos_log() -> Path:
    """The default macOS log file; the app also writes here when it cannot read its config."""
    return _path(consts.MACOS_LOG_DIR) / consts.LOG_NAME


def _log_file(value: str, state_dir: Path) -> Path | None:
    if not value:
        return None
    if value == consts.LOG_FILE_AUTO:
        return macos_log() if sys.platform == "darwin" else state_dir / consts.LOG_NAME
    return _path(value)


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    try:
        user = tomllib.loads(path.read_text()) if path.exists() else {}
        raw = _merge(tomllib.loads(DEFAULTS.read_text()), user)
        _check_ranges(raw)
        download = Download(**{**raw["download"], "destination": _path(raw["download"]["destination"])})
        if not download.destination.is_absolute():
            raise ValueError(f"download.destination must be an absolute path or start with ~, not {raw['download']['destination']!r}")
        download.folder(datetime.now())
        state_dir = _path(raw["state_dir"])
        networks = Networks(tuple(ipaddress.IPv4Network(n) for n in raw["networks"]))
        guid = uuid.UUID(raw["guid"]) if raw["guid"] else _itu2_guid() or _stored_guid(state_dir)
        log_level = raw["log_level"].upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ValueError(f"log_level must be one of {', '.join(logging.getLevelNamesMapping())}, not {raw['log_level']!r}")
    except (OSError, ValueError) as e:  # including tomllib.TOMLDecodeError
        raise ConfigError(f"{path}: {e}") from e
    return Config(
        download=download,
        hosts=raw["hosts"],
        networks=networks,
        name=raw["name"] or socket.gethostname().split(".")[0],
        guid=guid,
        advertise=raw["advertise"],
        port=raw["port"],
        announce_port=raw["announce_port"],
        state_dir=state_dir,
        log_file=_log_file(raw["log_file"], state_dir),
        log_level=log_level,
        timing=Timing(**raw["timing"]),
        ping_misses=raw["ping_misses"],
        path=path if path.exists() else None,
    )
