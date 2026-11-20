"""One camera session, run in a child process so the supervisor can kill it if it hangs.

Follows Canon Image Transfer Utility 2's auto-send flow (see canon.py):

  OpenSession(0x41) + ITU2 setup ops
  GetEvent (0x9116) -> 0xC1AB records: images the camera wants to send
  per image: 0x9170 info, 0x9174 chunked reads, then 0x91F0 + 0x9177 to
             acknowledge (the camera then marks it sent and offers the next)
  CompleteAutoSendImages (0x916D), mode resets, CloseSession

An image is acknowledged only after it is safely on disk, so any failure
before that makes the camera offer it again next time.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from . import canon, consts
from .canon import RC_OK
from .config import Config
from .ptpip import Client, ProtocolError
from .status import Event

log = logging.getLogger("cannonball.worker")

# Exit codes reported to the supervisor.
OK = 0
NO_CAMERA = 3  # never got a session within connect_window
FAILED = 4  # session broke part way; retry


class SessionError(Exception):
    pass


class Session:
    client_class = Client  # tests substitute a reference implementation

    def __init__(self, cfg: Config, host: str, heartbeat, notify):
        self.cfg = cfg
        self.host = host
        self.beat = heartbeat
        self.notify = notify  # progress events for the status display
        self.c: Client | None = None
        self.read_ready = False

    def op(self, code: int, *params: int) -> tuple[list[int], bytes]:
        rc, ps, data = self.c.op(code, *params)
        if rc != RC_OK:
            raise SessionError(f"op 0x{code:04X}{list(params)} failed: 0x{rc:04X}")
        return ps, data

    # -- connection ------------------------------------------------------

    def connect(self) -> bool:
        deadline = time.time() + self.cfg.timing.connect_window
        while time.time() < deadline:
            self.beat()
            c = self.client_class(
                self.host,
                self.cfg.guid,
                self.cfg.name,
                port=self.cfg.port,
                connect_timeout=self.cfg.timing.connect_timeout,
                op_timeout=self.cfg.timing.op_timeout,
            )
            try:
                c.connect()
            except (OSError, ProtocolError) as e:
                log.debug("%s: connect: %s", self.host, e)
                c.close()
                time.sleep(self.cfg.timing.connect_retry)
                continue
            self.c = c
            return True
        return False

    # -- transfer --------------------------------------------------------

    def destination(self, name: str, when: datetime, size: int) -> tuple[Path, bool]:
        """Where to save; True if an identical-size file is already there."""
        sub = self.cfg.download.subfolder.format(date=when) if self.cfg.download.subfolder else ""
        dest = self.cfg.download.destination / sub / name
        n = 1
        while dest.exists():
            if dest.stat().st_size == size:
                return dest, True
            dest = dest.with_name(f"{Path(name).stem}_{n}{Path(name).suffix}")
            n += 1
        return dest, False

    def transfer(self, handle: int, size: int, path: str) -> None:
        _, raw = self.op(canon.OBJECT_INFO, handle)
        info = canon.object_info(raw)
        name = info.filename if info and info.filename else Path(path.replace("\\", "/")).name
        if info and info.size:
            size = info.size
        if size <= 0:
            # Never save (and acknowledge) an empty file; leave the image on the
            # camera's send list and fail the session so it is retried.
            raise SessionError(f"{name}: camera reported size {size}")
        when = (info and canon.capture_time(info.time1)) or datetime.now()
        dest, have = self.destination(name, when, size)

        if not have:
            self.notify(event=Event.RECEIVING, file=name)
            self.download(handle, size, dest)
            self.notify(event=Event.SAVED, path=str(dest), size=size)
            os.utime(dest, (when.timestamp(), when.timestamp()))
        else:
            log.info("%s already saved as %s", name, dest)
        # Only now tell the camera the image is done.
        self.op(canon.TRANSFER_COMPLETE, handle)
        self.op(canon.SAVE_COMPLETE, handle, 0)

    def download(self, handle: int, size: int, dest: Path) -> None:
        if not self.read_ready:
            for code, params in canon.BEFORE_FIRST_READ:  # response codes don't matter
                self.c.op(code, *params)
            self.read_ready = True

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        # Always from the start: the 90D serves an image as a stream within a
        # session, and a new session's read at a later offset never answers
        # (the camera then cancels the transfer and turns Wi-Fi off).
        offset = 0
        started = time.monotonic()
        with part.open("wb") as f:
            while offset < size:
                want = min(self.cfg.download.chunk_size, size - offset)
                _, data = self.op(canon.READ, handle, offset, want, 0, 0)
                self.c.op(canon.STATUS, canon.STORAGE)
                if not data:
                    raise SessionError(f"{dest.name}: no data at offset {offset}")
                f.write(data[: size - offset])
                offset += min(len(data), size - offset)
                self.beat()
                self.c.op(canon.PROGRESS, handle, min(canon.PROGRESS_DONE, canon.PROGRESS_DONE * offset // size))
            f.flush()
            os.fsync(f.fileno())
        if part.stat().st_size != size:
            raise SessionError(f"{dest.name}: got {part.stat().st_size} bytes, expected {size}")
        part.replace(dest)
        elapsed = time.monotonic() - started
        log.info("saved %s (%.1f MiB in %.1fs)", dest, size / consts.MIB, elapsed)

    # -- the session -----------------------------------------------------

    def run(self) -> int:
        if not self.connect():
            log.info("%s: no PTP/IP session within %.0fs", self.host, self.cfg.timing.connect_window)
            return NO_CAMERA
        c = self.c
        try:
            log.info("%s: connected to %s", self.host, c.camera_name or "camera")
            # Like ITU2, send the whole setup before looking at response codes.
            failed = []
            events = None
            for code, params in [(canon.OPEN_SESSION, (canon.SESSION_ID,)), *canon.SETUP]:
                rc, _, data = c.op(code, *params)
                if rc != RC_OK:
                    failed.append(f"0x{code:04X}: 0x{rc:04X}")
                if code == canon.GET_EVENT and events is None:
                    events = data  # lists the images waiting to be sent
            if failed:
                raise SessionError(f"setup failed ({', '.join(failed)})")
            self.beat()
            self.notify(event=Event.CONNECTED, camera=c.camera_name)

            count = 0
            idle_since = time.time()
            while True:
                objects = canon.objects(events)
                for obj in objects:
                    self.transfer(obj.handle, obj.size, obj.path)
                    count += 1
                    idle_since = time.time()
                if not objects:
                    if time.time() - idle_since > self.cfg.timing.idle_timeout:
                        break
                    time.sleep(self.cfg.timing.event_poll)
                self.beat()
                _, events = self.op(canon.GET_EVENT)

            log.info("%s: nothing more to send; %d image(s) received", self.host, count)
            self.notify(event=Event.FINISHED, count=count)
            for code, params in canon.FINISH:  # sent in full whatever the response codes
                rc, _, _ = c.op(code, *params)
                log.debug("finish 0x%04X -> 0x%04X", code, rc)
            return OK
        finally:
            c.close()


def main(cfg: Config, host: str, heartbeat, log_queue, events) -> None:
    """Child-process entry point (multiprocessing, spawn)."""
    import logging.handlers
    import sys

    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(log_queue)]
    root.setLevel(cfg.log_level)

    def beat():
        heartbeat.value = time.time()

    def notify(**event):
        events.put(event)

    try:
        code = Session(cfg, host, beat, notify).run()
    except Exception as e:  # noqa: BLE001 - report every failure to the supervisor and retry
        log.warning("%s: session failed: %s", host, e, exc_info=log.isEnabledFor(logging.DEBUG))
        notify(event=Event.ERROR, message=f"session failed: {e}")
        code = FAILED
    sys.exit(code)
