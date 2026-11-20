"""What the daemon is doing, in state_dir/status.json, for the menu bar app."""

import json
import os
import time
from enum import StrEnum
from pathlib import Path

from . import consts


class State(StrEnum):
    WAITING = "waiting"  # for the camera
    CAMERA = "camera"  # on the network, connecting
    RECEIVING = "receiving"
    DOWN = "down"  # daemon not running (seen by readers only)


class Event(StrEnum):
    """Progress reported by a worker."""

    CONNECTED = "connected"
    RECEIVING = "receiving"
    SAVED = "saved"
    FINISHED = "finished"
    ERROR = "error"


def status_path(state_dir: Path) -> Path:
    return state_dir / consts.STATE_STATUS


def read(state_dir: Path) -> dict:
    try:
        return json.loads(status_path(state_dir).read_text())
    except (OSError, ValueError):
        return {}


def daemon_alive(status: dict) -> bool:
    pid = status.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Status:
    """The daemon's view, persisted for the menu bar app."""

    def __init__(self, state_dir: Path):
        self.path = status_path(state_dir)
        self.data = {
            "pid": os.getpid(),
            "state": State.WAITING,
            "camera": None,
            "file": None,
            "received": 0,  # images in the current or last session
            "last_session": read(state_dir).get("last_session"),  # {"time", "count"}
            "error": None,
        }
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)

    def set(self, **changes) -> None:
        if any(self.data.get(k) != v for k, v in changes.items()):
            self.data.update(changes)
            self._write()

    def event(self, ev: dict) -> None:
        """Apply a progress event sent by a worker."""
        kind = ev.get("event")
        if kind == Event.CONNECTED:
            self.set(state=State.RECEIVING, camera=ev.get("camera"), received=0, file=None, error=None)
        elif kind == Event.RECEIVING:
            self.set(file=ev.get("file"))
        elif kind == Event.SAVED:
            self.set(received=self.data["received"] + 1, file=None)
        elif kind == Event.FINISHED:
            self.set(last_session={"time": time.time(), "count": ev.get("count", 0)}, file=None)
        elif kind == Event.ERROR:
            self.set(error=ev.get("message"))
