"""What the supervisor is doing, for the menu bar."""

import threading
import time
from dataclasses import dataclass, replace
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
    REFUSED = "refused"  # the camera answered pings, but the system refused our connections


@dataclass(frozen=True)
class Progress:
    """One worker report, sent to the supervisor through a queue."""

    event: Event
    file: str | None = None  # RECEIVING
    count: int = 0  # FINISHED: images received in the session
    message: str | None = None  # ERROR


@dataclass(frozen=True)
class LastSession:
    time: float
    count: int


@dataclass(frozen=True)
class Snapshot:
    state: State = State.WAITING
    file: str | None = None  # being received
    received: int = 0  # images in the current or last session
    last_session: LastSession | None = None
    error: str | None = None
    refused: bool = False


class Status:
    """Written by the supervisor thread, read by the menu bar."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = Snapshot()

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def set(self, **changes) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    def event(self, progress: Progress) -> None:
        """Apply a worker's report."""
        match progress.event:
            case Event.CONNECTED:
                self.set(state=State.RECEIVING, received=0, file=None, error=None, refused=False)
            case Event.RECEIVING:
                self.set(file=progress.file)
            case Event.SAVED:
                with self._lock:
                    self._snapshot = replace(self._snapshot, received=self._snapshot.received + 1, file=None)
            case Event.FINISHED:
                self.set(last_session=LastSession(time.time(), progress.count), file=None)
            case Event.ERROR:
                self.set(error=progress.message)
            case Event.REFUSED:
                self.set(refused=True)
