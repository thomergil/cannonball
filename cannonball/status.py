"""What the supervisor is doing, for the menu bar."""

import threading
import time
from enum import StrEnum


class State(StrEnum):
    WAITING = "waiting"  # for the camera
    CAMERA = "camera"  # on the network, connecting
    RECEIVING = "receiving"
    DOWN = "down"  # the supervisor stopped (seen by the menu bar only)


class Event(StrEnum):
    """Progress reported by a worker."""

    CONNECTED = "connected"
    RECEIVING = "receiving"
    SAVED = "saved"
    FINISHED = "finished"
    ERROR = "error"


class Status:
    """Written by the supervisor thread, read by the menu bar."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "state": State.WAITING,
            "file": None,
            "received": 0,  # images in the current or last session
            "last_session": None,  # {"time", "count"}
            "error": None,
        }

    @property
    def data(self) -> dict:
        with self._lock:
            return dict(self._data)

    def set(self, **changes) -> None:
        with self._lock:
            self._data.update(changes)

    def event(self, ev: dict) -> None:
        """Apply a progress event sent by a worker."""
        kind = ev.get("event")
        if kind == Event.CONNECTED:
            self.set(state=State.RECEIVING, received=0, file=None, error=None)
        elif kind == Event.RECEIVING:
            self.set(file=ev.get("file"))
        elif kind == Event.SAVED:
            self.set(received=self.data["received"] + 1, file=None)
        elif kind == Event.FINISHED:
            self.set(last_session={"time": time.time(), "count": ev.get("count", 0)}, file=None)
        elif kind == Event.ERROR:
            self.set(error=ev.get("message"))
