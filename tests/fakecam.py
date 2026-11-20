"""A fake Canon EOS camera in auto-send mode, speaking PTP/IP on localhost.

Written from the protocol, independently of cannonball's client: its own
constants and encoders. It models what the EOS 90D was seen to do:

* offers one image at a time (a 0xC1AB record in GetEvent data), and the
  next only after the previous one was acknowledged with 0x9177;
* re-offers an unacknowledged image in the next session;
* serves an image as a stream: a session's first read of an image must start
  at offset 0, any other offset is never answered (the 90D hangs, then
  cancels and turns Wi-Fi off).

Everything the client sends is recorded per channel, so two clients can be
compared byte for byte. Faults are switched on with constructor arguments.
"""

import random
import socket
import struct
import threading
import uuid
from dataclasses import dataclass

# Packet types, CIPA DC-X005.
INIT_COMMAND_REQUEST, INIT_COMMAND_ACK, INIT_EVENT_REQUEST, INIT_EVENT_ACK, INIT_FAIL = 1, 2, 3, 4, 5
OPERATION_REQUEST, OPERATION_RESPONSE, EVENT, START_DATA, DATA, CANCEL, END_DATA = 6, 7, 8, 9, 10, 11, 12
PROBE_REQUEST, PROBE_RESPONSE = 13, 14

OK, GENERAL_ERROR, INVALID_HANDLE = 0x2001, 0x2002, 0x2009
STORAGE, ROOT = 0x00020001, 0x91900000
CAMERA_GUID = uuid.UUID("00000000-0000-4000-8000-0000000090d0")


@dataclass
class Image:
    path: str  # on the camera, B:/DCIM/100CANON/IMG_0001.JPG
    data: bytes
    time1: str = "20260708T091035.0"
    time2: str = "20260708T091034.0"
    format: int = 0x3801
    size: int | None = None  # size the camera reports, if not len(data)
    handle: int = 0

    @property
    def reported_size(self) -> int:
        return len(self.data) if self.size is None else self.size

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def pkt(kind: int, body: bytes = b"") -> bytes:
    return struct.pack("<II", len(body) + 8, kind) + body


def p16(text: str) -> bytes:
    """u8 length in UTF-16 code units (with NUL), then UTF-16LE text and NUL."""
    return bytes([len(text) + 1]) + (text + "\0").encode("utf-16le")


class Hang(Exception):
    """Stop answering on this connection (the client has to time out)."""


class Drop(Exception):
    """Close this connection now."""


class FakeCamera:
    def __init__(
        self,
        paired: uuid.UUID,
        images: list[Image],
        *,
        name: str = "EOS90D_TEST",
        port: int = 0,
        packet_size: int = 65536,  # payload per Data packet
        fragment: bool = False,  # write in tiny random pieces
        interleave_events: bool = False,  # Event packets on the command channel
        probe: bool = False,  # Probe_Request on both channels
        drop_after: int | None = None,  # drop the connection after this many image bytes (once)
        hang_on: tuple[int, int] | None = None,  # (opcode, n): stop answering at its n-th request (once)
        chatter: float | None = None,  # while hanging, send an Event packet this often (seconds)
        fail_on: dict[int, int] | None = None,  # opcode -> response code
        raw_on: dict[int, bytes] | None = None,  # opcode -> bytes sent instead of a response
        short_reads: bool = False,  # answer reads with half the bytes asked for
        seed: int = 0,
    ):
        self.paired = paired
        self.name = name
        self.pending = list(images)
        for i, img in enumerate(images):
            img.handle = img.handle or 0x91903B82 + 4 * i
        self.images = {img.handle: img for img in images}
        self.packet_size = packet_size
        self.fragment = fragment
        self.interleave_events = interleave_events
        self.probe = probe
        self.drop_after = drop_after
        self.hang_on = hang_on
        self.chatter = chatter
        self.fail_on = fail_on or {}
        self.raw_on = raw_on or {}
        self.short_reads = short_reads
        self.rng = random.Random(seed)

        self.cmd_stream = bytearray()  # everything the client sent, per channel
        self.event_stream = bytearray()
        self.ops: list[tuple[int, tuple[int, ...]]] = []
        self.acked: list[int] = []
        self.sessions = 0
        self.probes_answered = 0
        self.image_bytes_sent = 0
        self.on_ack = None  # called with the Image when it is acknowledged
        self.errors: list[str] = []  # protocol violations by the client
        self._counts: dict[int, int] = {}
        self._lock = threading.Lock()
        self._conns: list[socket.socket] = []
        self._closed = threading.Event()

        self.server = socket.create_server(("127.0.0.1", port))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    # -- plumbing --------------------------------------------------------

    def close(self) -> None:
        self._closed.set()
        self.server.close()
        for c in self._conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _recv(self, conn, n: int, stream: bytearray) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        with self._lock:
            stream += buf
        return buf

    def _read(self, conn, stream) -> tuple[int, bytes]:
        length, kind = struct.unpack("<II", self._recv(conn, 8, stream))
        return kind, self._recv(conn, length - 8, stream)

    def _send(self, conn, data: bytes) -> None:
        if not self.fragment:
            conn.sendall(data)
            return
        pos = 0
        while pos < len(data):
            n = self.rng.randint(1, 7)
            conn.sendall(data[pos : pos + n])
            pos += n

    def _hang(self, conn, stream) -> None:
        """Never answer (only chatter, if asked); keep recording until the client gives up."""
        quiet = threading.Event()
        if self.chatter:
            def chat():
                while not quiet.wait(self.chatter):
                    try:
                        self._send(conn, pkt(EVENT, struct.pack("<HI", 0xC189, 0)))
                    except OSError:
                        return

            threading.Thread(target=chat, daemon=True).start()
        try:
            while self._recv(conn, 1, stream):
                pass
        except (EOFError, OSError):
            pass
        finally:
            quiet.set()

    def _serve(self, conn) -> None:
        stream = bytearray()  # until we know which channel this is
        try:
            kind, body = self._read(conn, stream)
            if kind == INIT_COMMAND_REQUEST:
                with self._lock:
                    self.cmd_stream += stream
                self._command(conn, body)
            elif kind == INIT_EVENT_REQUEST:
                with self._lock:
                    self.event_stream += stream
                self._event(conn)
            else:
                self.errors.append(f"first packet type {kind}")
        except (EOFError, OSError, Drop):
            pass
        finally:
            conn.close()

    # -- event channel ---------------------------------------------------

    def _event(self, conn) -> None:
        self._send(conn, pkt(INIT_EVENT_ACK))
        if self.probe:
            self._send(conn, pkt(PROBE_REQUEST))
        while True:
            kind, _ = self._read(conn, self.event_stream)
            if kind == PROBE_RESPONSE:
                self.probes_answered += 1

    # -- command channel -------------------------------------------------

    def _command(self, conn, body: bytes) -> None:
        guid = uuid.UUID(bytes=body[:16])
        if guid != self.paired:
            self._send(conn, pkt(INIT_FAIL, struct.pack("<I", 1)))
            return
        version = body[-4:]
        if struct.unpack("<I", version)[0] != 0x00010000:
            self.errors.append(f"version {version.hex()}")
        self._send(conn, pkt(INIT_COMMAND_ACK, struct.pack("<I", 1) + CAMERA_GUID.bytes + (self.name + "\0").encode("utf-16le") + version))
        self.sessions += 1
        session = {"first_event": True, "offered": None, "stream": {}, "closed": False, "tid": 0}
        if self.probe:
            self._send(conn, pkt(PROBE_REQUEST))
        while True:
            kind, body = self._read(conn, self.cmd_stream)
            if kind == PROBE_RESPONSE:
                self.probes_answered += 1
                continue
            if kind != OPERATION_REQUEST:
                self.errors.append(f"packet type {kind} on the command channel")
                continue
            phase, code, tid = struct.unpack_from("<IHI", body)
            params = struct.unpack_from(f"<{(len(body) - 10) // 4}I", body, 10)
            if phase != 1:
                self.errors.append(f"data phase {phase} for 0x{code:04X}")
            if tid != session["tid"] + 1:
                self.errors.append(f"transaction {tid} after {session['tid']}")
            session["tid"] = tid
            self.ops.append((code, params))
            try:
                self._operation(conn, session, code, tid, params)
            except Hang:
                self._hang(conn, self.cmd_stream)
                return

    def _respond(self, conn, tid, code=OK, params=(), data=None) -> None:
        if self.interleave_events:
            self._send(conn, pkt(EVENT, struct.pack("<HI", 0xC1A0, 0)))
        if data is not None:
            self._send(conn, pkt(START_DATA, struct.pack("<IQ", tid, len(data))))
            pieces = [data[i : i + self.packet_size] for i in range(0, len(data), self.packet_size)] or [b""]
            for i, piece in enumerate(pieces):
                last = i == len(pieces) - 1
                self._send(conn, pkt(END_DATA if last else DATA, struct.pack("<I", tid) + piece))
        self._send(conn, pkt(OPERATION_RESPONSE, struct.pack("<HI", code, tid) + struct.pack(f"<{len(params)}I", *params)))

    def _operation(self, conn, session, code, tid, params) -> None:
        n = self._counts[code] = self._counts.get(code, 0) + 1
        if self.hang_on and self.hang_on == (code, n):
            self.hang_on = None
            raise Hang
        if code in self.raw_on:
            self._send(conn, self.raw_on.pop(code))
            return
        if code in self.fail_on:
            self._respond(conn, tid, self.fail_on[code])
            return

        if code == 0x9116:  # GetEvent
            if session["closed"]:
                return self._respond(conn, tid, GENERAL_ERROR)
            records = b""
            if session["first_event"]:
                session["first_event"] = False
                for prop in (0xD1A6, 0xD169, 0xD16A):
                    records += struct.pack("<IIII", 16, 0xC189, prop, 1)
                records += struct.pack("<III", 12, 0xC1A4, STORAGE)
            if session["offered"] is None and self.pending:
                img = self.pending[0]
                session["offered"] = img.handle
                body = struct.pack("<6I", img.handle, img.format, 0x20, img.reported_size, 0, img.handle - 2)
                body += img.path.encode("ascii") + b"\0"
                records += struct.pack("<II", 8 + len(body), 0xC1AB) + body
            records += struct.pack("<II", 8, 0)
            return self._respond(conn, tid, data=records)

        if code == 0x9170:  # object info
            (handle,) = params
            if handle == ROOT:
                return self._respond(conn, tid, data=struct.pack("<3I", STORAGE, 0x3001, 0).ljust(150, b"\0"))
            img = self.images.get(handle)
            if img is None:
                return self._respond(conn, tid, INVALID_HANDLE)
            info = struct.pack("<3I", STORAGE, img.format, img.reported_size).ljust(0x38, b"\0")
            info += p16(img.name) + p16(img.time1) + p16(img.time2)
            return self._respond(conn, tid, data=info.ljust(158, b"\0"))

        if code == 0x9174:  # read [handle, offset, length, 0, 0]
            handle, offset, length = params[:3]
            img = self.images.get(handle)
            if img is None or handle != session["offered"]:
                return self._respond(conn, tid, INVALID_HANDLE)
            if offset != session["stream"].get(handle, 0):
                raise Hang  # the 90D cannot seek: it never answers
            if self.short_reads:
                length = max(1, length // 2)
            chunk = img.data[offset : offset + length]
            if self.drop_after is not None and self.image_bytes_sent + len(chunk) > self.drop_after:
                keep = self.drop_after - self.image_bytes_sent
                self.drop_after = None
                self._send(conn, pkt(START_DATA, struct.pack("<IQ", tid, len(chunk))))
                self._send(conn, pkt(DATA, struct.pack("<I", tid) + chunk[:keep]))
                raise Drop
            self.image_bytes_sent += len(chunk)
            session["stream"][handle] = offset + len(chunk)
            remaining = len(img.data) - offset - len(chunk)
            return self._respond(conn, tid, params=(len(chunk), remaining, 0), data=chunk)

        if code == 0x9102:
            return self._respond(conn, tid, data=struct.pack("<I", STORAGE).ljust(36, b"\0"))
        if code == 0x9101:
            return self._respond(conn, tid, data=bytes(8))
        if code == 0x913F:
            return self._respond(conn, tid, params=(0x80000436, 1))
        if code == 0x9177:  # [handle, 0]: the image is done
            handle = params[0]
            if handle == session["offered"]:
                img = self.images[handle]
                if self.on_ack:
                    self.on_ack(img)
                self.acked.append(handle)
                self.pending.remove(img)
                session["offered"] = None
            return self._respond(conn, tid)
        if code == 0x1003:
            session["closed"] = True
        return self._respond(conn, tid)
