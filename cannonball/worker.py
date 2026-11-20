"""One camera session, run in a child process so the supervisor can kill it if it hangs.

Follows Canon Image Transfer Utility 2's auto-send flow (see canon.py):

  OpenSession(0x41) + ITU2 setup ops
  GetEvent (0x9116) -> 0xC1AB records: images the camera wants to send
  per image: 0x9170 info, 0x9174 chunked reads, then 0x91F0 + 0x9177 to
             acknowledge (the camera then marks it sent and offers the next)
  CompleteAutoSendImages (0x916D), mode resets, CloseSession

An image is acknowledged only after its file and folder are flushed to disk,
so any failure before that makes the camera offer it again next time. The
camera is untrusted: its file names never choose where a file is written.
"""

import errno
import fcntl
import functools
import logging
import logging.handlers
import os
import shutil
import sys
import time
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from . import canon, consts
from .canon import RC_OK
from .config import Config
from .ptpip import Client, ProtocolError
from .status import Event, Progress

log = logging.getLogger("cannonball.worker")

# Exit codes reported to the supervisor.
OK = 0
NO_CAMERA = 3  # never got a session within connect_window
FAILED = 4  # session broke part way; retry
NO_SPACE = 5  # the disk is full; retry after consts.NO_SPACE_RETRY

# errno values with which fcntl(F_FULLFSYNC) or fsync() reports that the file system does not support the call.
UNSUPPORTED = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL}


class SessionError(Exception):
    pass


class NoSpace(SessionError):
    """The download folder's disk is too full; the image waits on the camera."""


def flush_to_disk(fd: int) -> None:
    """Make a file's (or folder's) data survive a power loss. On macOS, fsync()
    leaves it in the drive's cache and F_FULLFSYNC does not (fsync(2)); network
    file systems refuse F_FULLFSYNC, and some refuse to sync a folder at all."""
    if sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError as e:
            if e.errno not in UNSUPPORTED:
                raise
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in UNSUPPORTED:
            raise
        _warn_unflushed(e.strerror)  # once per session


@functools.cache
def _warn_unflushed(reason: str) -> None:
    log.warning("the file system cannot flush to disk (%s); saving without a flush", reason)


def set_times(fd: int, when: datetime) -> None:
    """Give the file its capture time, if that time is one the system can store."""
    try:
        stamp = when.timestamp()
        os.utime(fd, (stamp, stamp))
    except (OverflowError, ValueError, OSError) as e:
        log.debug("keeping the current file time: %s", e)


class Session:
    client_class = Client  # tests substitute a reference implementation

    def __init__(self, cfg: Config, host: str, heartbeat, notify):
        self.cfg = cfg
        self.host = host
        self.beat = heartbeat
        self.notify = notify  # progress events for the status display
        self.c: Client | None = None
        self.connect_error: int | None = None
        self.read_ready = False

    def op(self, code: int, *params: int, timeout: float | None = None) -> tuple[list[int], bytes]:
        rc, ps, data = self.c.op(code, *params, timeout=timeout)
        if rc != RC_OK:
            raise SessionError(f"op 0x{code:04X}{list(params)} failed: 0x{rc:04X}")
        return ps, data

    # -- connection ------------------------------------------------------

    def connect(self) -> bool:
        deadline = time.monotonic() + self.cfg.timing.connect_window
        while time.monotonic() < deadline:
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
                self.connect_error = getattr(e, "errno", None)
                c.close()
                time.sleep(self.cfg.timing.connect_retry)
                continue
            self.c = c
            return True
        return False

    # -- transfer --------------------------------------------------------

    def log_data(self, code: int, data: bytes) -> None:
        """At DEBUG level, log the start of each reply's data and of each GetEvent
        record other than property records, for protocol study."""
        if not (data and log.isEnabledFor(logging.DEBUG)):
            return
        if code != canon.GET_EVENT:
            log.debug("0x%04X data: %s", code, data[: canon.RECORD_LOG_BYTES].hex())
            return
        for kind, body in canon.events(data):
            if kind not in canon.PROPERTY_RECORDS:
                log.debug("record 0x%04X: %s", kind, body[: canon.RECORD_LOG_BYTES].hex())

    def destination(self, name: str, when: datetime, size: int) -> tuple[Path, bool]:
        """Where to save; True if an identical-size file is already there."""
        try:
            dest = self.cfg.download.folder(when) / name  # name: canon.file_name, a plain file name
        except ValueError as e:
            raise SessionError(str(e)) from e
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
        name = canon.file_name(handle, info.filename if info else None, path)
        if info and info.size:
            size = info.size
        if size <= 0:
            # Never save (and acknowledge) an empty file; leave the image on the
            # camera's send list and fail the session so it is retried.
            raise SessionError(f"{name}: camera reported size {size}")
        when = (info and canon.capture_time(info.time1)) or datetime.now()
        dest, have = self.destination(name, when, size)

        if not have:
            dest.parent.mkdir(parents=True, exist_ok=True)
            reserve = self.cfg.download.reserve
            if shutil.disk_usage(dest.parent).free < size + reserve:
                raise NoSpace(f"{name}: not enough free space (keeping {reserve // consts.MIB} MiB free)")
            self.notify(Progress(Event.RECEIVING, file=name))
            self.download(handle, size, dest, when)
            self.notify(Progress(Event.SAVED))
        else:
            log.info("%s already saved as %s", name, dest)
        # Only now tell the camera the image is done.
        self.op(canon.TRANSFER_COMPLETE, handle)
        self.op(canon.SAVE_COMPLETE, handle, 0)

    def download(self, handle: int, size: int, dest: Path, when: datetime) -> None:
        if not self.read_ready:
            for code, params in canon.BEFORE_FIRST_READ:  # response codes don't matter
                self.c.op(code, *params)
            self.read_ready = True

        # No saved image has this name: canon.file_name refuses a leading dot.
        part = dest.with_name(consts.PARTIAL_NAME.format(name=dest.name))
        if part.exists():
            if not self.abandoned(part):
                raise SessionError(f"{dest.name}: another Cannonball is saving it")
            part.unlink(missing_ok=True)
        record = self.cfg.state_dir / consts.STATE_PARTIAL
        record.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        # Exclusive: never follows a link, and fails (leaving the file alone) if another writer just made it.
        with part.open("xb") as f:
            record.write_text(str(part))
            try:
                self.receive(handle, size, f, dest.name)
                set_times(f.fileno(), when)
                flush_to_disk(f.fileno())
                part.replace(dest)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        record.unlink(missing_ok=True)
        folder = os.open(dest.parent, os.O_RDONLY)
        try:
            flush_to_disk(folder)  # the rename itself
        finally:
            os.close(folder)
        log.info("saved %s (%.1f MiB in %.1fs)", dest, size / consts.MIB, time.monotonic() - started)

    def receive(self, handle: int, size: int, f, name: str) -> None:
        """Read the whole image into the open file f."""
        # Always from the start: the 90D serves an image as a stream within a
        # session, and a new session's read at a later offset never answers
        # (the camera then cancels the transfer and turns Wi-Fi off).
        offset = 0
        while offset < size:
            want = min(self.cfg.download.chunk_size, size - offset)
            _, data = self.op(canon.READ, handle, offset, want, 0, 0, timeout=self.cfg.timing.read_timeout)
            self.c.op(canon.STATUS, canon.STORAGE)
            if not data:
                raise SessionError(f"{name}: no data at offset {offset}")
            f.write(data[: size - offset])
            offset += min(len(data), size - offset)
            self.beat()
            self.c.op(canon.PROGRESS, handle, min(canon.PROGRESS_DONE, canon.PROGRESS_DONE * offset // size))
        f.flush()

    def abandoned(self, part: Path) -> bool:
        """True if nobody has written to part for stall_timeout (a session that
        was still writing it would have been stopped by now), or its time is in
        the future (a clock was set back)."""
        age = time.time() - part.lstat().st_mtime
        return age > self.cfg.timing.stall_timeout or age < 0

    def remove_partial(self) -> None:
        """Remove the temp file of the last session, if it was killed mid-download.

        Only one session runs per state_dir, so a recorded file is no longer written."""
        record = self.cfg.state_dir / consts.STATE_PARTIAL
        try:
            part = Path(record.read_text())
            inside = part.resolve().is_relative_to(self.cfg.download.destination.resolve())
            if inside and fnmatch(part.name, consts.PARTIAL_NAME.format(name="*")):
                log.info("removing %s, left by an interrupted session", part)
                part.unlink(missing_ok=True)
        except FileNotFoundError:
            return  # no record: the last session ended cleanly
        except (OSError, ValueError) as e:  # unreadable or damaged record
            log.warning("ignoring %s: %s", record, e)
        record.unlink(missing_ok=True)

    # -- the session -----------------------------------------------------

    def run(self) -> int:
        self.remove_partial()
        if not self.connect():
            log.info("%s: no PTP/IP session within %.0fs", self.host, self.cfg.timing.connect_window)
            if self.connect_error == errno.EHOSTUNREACH and sys.platform == "darwin":
                # The camera answers pings, but macOS refuses our connections.
                log.warning("%s", consts.LOCAL_NETWORK_HINT)
                self.notify(Progress(Event.REFUSED))
            return NO_CAMERA
        c = self.c
        try:
            log.info("%s: connected to %s", self.host, c.camera_name or "camera")
            # Like ITU2, send the whole setup before looking at response codes.
            failed = []
            events = None
            for code, params in [(canon.OPEN_SESSION, (canon.SESSION_ID,)), *canon.SETUP]:
                rc, _, data = c.op(code, *params)
                self.log_data(code, data)
                if rc != RC_OK:
                    failed.append(f"0x{code:04X}: 0x{rc:04X}")
                if code == canon.GET_EVENT and events is None:
                    events = data  # lists the images waiting to be sent
            if failed:
                raise SessionError(f"setup failed ({', '.join(failed)})")
            self.beat()
            self.notify(Progress(Event.CONNECTED))

            count = 0
            idle_since = time.monotonic()
            while True:
                objects = canon.objects(events)
                for obj in objects:
                    self.transfer(obj.handle, obj.size, obj.path)
                    count += 1
                    idle_since = time.monotonic()
                if not objects:
                    if time.monotonic() - idle_since > self.cfg.timing.idle_timeout:
                        break
                    time.sleep(self.cfg.timing.event_poll)
                self.beat()
                _, events = self.op(canon.GET_EVENT)
                self.log_data(canon.GET_EVENT, events)

            log.info("%s: nothing more to send; %d image(s) received", self.host, count)
            self.notify(Progress(Event.FINISHED, count=count))
            for code, params in canon.FINISH:  # sent in full whatever the response codes
                rc, _, _ = c.op(code, *params)
                log.debug("finish 0x%04X -> 0x%04X", code, rc)
            return OK
        finally:
            c.close()


def main(cfg: Config, host: str, heartbeat, log_queue, events) -> None:
    """Child-process entry point (multiprocessing, spawn)."""
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(log_queue)]
    root.setLevel(cfg.log_level)

    def beat():
        heartbeat.value = time.time()

    def notify(progress: Progress) -> None:
        events.put(progress)

    try:
        code = Session(cfg, host, beat, notify).run()
    except NoSpace as e:
        log.warning("%s: %s", host, e)
        notify(Progress(Event.ERROR, message=str(e)))
        code = NO_SPACE
    except Exception as e:  # noqa: BLE001 - report every failure to the supervisor and retry
        log.warning("%s: session failed: %s", host, e, exc_info=log.isEnabledFor(logging.DEBUG))
        notify(Progress(Event.ERROR, message=f"session failed: {e}"))
        code = FAILED
    sys.exit(code)
