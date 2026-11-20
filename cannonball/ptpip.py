"""Minimal PTP/IP initiator (CIPA DC-X005), as needed for Canon auto-send.

Two TCP connections to the camera: a command connection that carries
operation requests, data and responses, and an event connection that is only
kept open (Canon reports events through the GetEvent operation instead) and
answered when the camera probes it.

All integers are little-endian. Every packet is [u32 length][u32 type][body],
with the length including the 8-byte header.
"""

import logging
import socket
import struct
import threading
import time
import uuid

log = logging.getLogger(__name__)

VERSION = 0x00010000

INIT_COMMAND_REQUEST = 1
INIT_COMMAND_ACK = 2
INIT_EVENT_REQUEST = 3
INIT_EVENT_ACK = 4
INIT_FAIL = 5
OPERATION_REQUEST = 6
OPERATION_RESPONSE = 7
EVENT = 8
START_DATA = 9
DATA = 10
CANCEL = 11
END_DATA = 12
PROBE_REQUEST = 13
PROBE_RESPONSE = 14

DATA_PHASE_NONE_OR_IN = 1  # we never send data to the camera
# Bounds on what an untrusted peer can make us allocate.
MAX_DATA = 16 * 1024 * 1024  # one data phase, and so one Data packet; bounds download.chunk_size_mb
MAX_CONTROL = 4096  # any other packet
MAX_PARAMS = 5  # per operation response (PIMA 15740)
MAX_CAMERA_NAME = 64  # characters kept of the name the camera reports
DATA_CARRIERS = {DATA, END_DATA}
DATA_HEADER = 4  # transaction id before a Data or End_Data payload


class ProtocolError(Exception):
    pass


def packet(kind: int, body: bytes = b"") -> bytes:
    return struct.pack("<II", 8 + len(body), kind) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:])
        if not k:
            raise ProtocolError("connection closed by camera")
        got += k
    return bytes(buf)


def recv_packet(sock: socket.socket, max_data: int = MAX_DATA) -> tuple[int, bytes]:
    """One packet; Data and End_Data may carry max_data bytes, others MAX_CONTROL."""
    length, kind = struct.unpack("<II", _recv_exact(sock, 8))
    limit = DATA_HEADER + max_data if kind in DATA_CARRIERS else MAX_CONTROL
    if not 8 <= length <= 8 + limit:
        raise ProtocolError(f"bad length {length} for packet type {kind}")
    return kind, _recv_exact(sock, length - 8)


def unpack(fmt: str, body: bytes, offset: int = 0) -> tuple:
    """struct.unpack_from, with a short packet as a ProtocolError."""
    try:
        return struct.unpack_from(fmt, body, offset)
    except struct.error as e:
        raise ProtocolError(f"short packet: {e}") from e


def printable(text: str, limit: int) -> str:
    """A camera-supplied string, safe to log and display."""
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def _utf16z(text: str) -> bytes:
    return (text + "\0").encode("utf-16le")


class Client:
    """One PTP/IP connection to a camera.

    op() returns (response code, response params, data) and does not raise on
    a non-OK response code; it raises ProtocolError when the camera breaks the
    protocol, TimeoutError when an operation goes op_timeout seconds without
    progress, and OSError when the connection fails.
    """

    def __init__(self, host: str, guid: uuid.UUID, name: str, *, port: int, connect_timeout: float, op_timeout: float):
        self.host = host
        self.port = port
        self.guid = guid
        self.name = name
        self.connect_timeout = connect_timeout
        self.op_timeout = op_timeout
        self.camera_name = ""
        self.camera_guid: uuid.UUID | None = None
        self.cmd: socket.socket | None = None
        self.event: socket.socket | None = None
        self._tid = 0
        self._closed = threading.Event()

    # -- connection ------------------------------------------------------

    def _open(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        return sock

    def connect(self) -> None:
        self.cmd = self._open()
        self.cmd.sendall(packet(INIT_COMMAND_REQUEST, self.guid.bytes + _utf16z(self.name) + struct.pack("<I", VERSION)))
        kind, body = recv_packet(self.cmd, max_data=MAX_CONTROL)
        if kind == INIT_FAIL:
            reason = struct.unpack_from("<I", body)[0] if len(body) >= 4 else None
            raise ProtocolError(f"camera refused the connection (Init_Fail, reason {reason})")
        if kind != INIT_COMMAND_ACK or len(body) < 24:
            raise ProtocolError(f"expected Init_Command_Ack, got packet type {kind}")
        (connection,) = unpack("<I", body)
        self.camera_guid = uuid.UUID(bytes=body[4:20])
        self.camera_name = printable(body[20:-4].decode("utf-16le", "replace").split("\0")[0], MAX_CAMERA_NAME)

        self.event = self._open()
        self.event.sendall(packet(INIT_EVENT_REQUEST, struct.pack("<I", connection)))
        kind, _ = recv_packet(self.event, max_data=MAX_CONTROL)
        if kind != INIT_EVENT_ACK:
            raise ProtocolError(f"expected Init_Event_Ack, got packet type {kind}")
        self.event.settimeout(None)
        threading.Thread(target=self._event_loop, name="ptpip-events", daemon=True).start()

    def _event_loop(self) -> None:
        """Answer probes on the event connection; events themselves are not used."""
        try:
            while not self._closed.is_set():
                kind, body = recv_packet(self.event, max_data=MAX_CONTROL)  # never carries data
                log.debug("<- event connection: packet type %d, %d bytes", kind, len(body))
                if kind == PROBE_REQUEST:
                    self.event.sendall(packet(PROBE_RESPONSE))
        except (OSError, ProtocolError):
            pass  # closed; the command connection notices on its own

    def close(self) -> None:
        self._closed.set()
        for sock in (self.cmd, self.event):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    # -- operations ------------------------------------------------------

    def op(self, code: int, *params: int) -> tuple[int, list[int], bytes]:
        self._tid += 1
        tid = self._tid
        body = struct.pack("<IHI", DATA_PHASE_NONE_OR_IN, code, tid) + struct.pack(f"<{len(params)}I", *params)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("-> 0x%04X %s", code, [hex(p) for p in params])
        self.cmd.settimeout(self.op_timeout)
        self.cmd.sendall(packet(OPERATION_REQUEST, body))

        data = bytearray()
        expected = None  # data length announced by Start_Data
        deadline = time.monotonic() + self.op_timeout  # moves only when this operation progresses
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no progress on operation 0x{code:04X} for {self.op_timeout:g}s")
            self.cmd.settimeout(remaining)
            kind, body = recv_packet(self.cmd)
            if kind == OPERATION_RESPONSE:
                rc, rtid = unpack("<HI", body)
                if rtid != tid:
                    raise ProtocolError(f"response for transaction {rtid}, expected {tid}")
                n = (len(body) - 6) // 4
                if n > MAX_PARAMS:
                    raise ProtocolError(f"response with {n} parameters")
                result = list(unpack(f"<{n}I", body, 6))
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("<- 0x%04X %s, %d bytes", rc, [hex(p) for p in result], len(data))
                return rc, result, bytes(data)
            if kind == START_DATA:
                if expected is not None:
                    raise ProtocolError(f"second Start_Data during operation 0x{code:04X}")
                dtid, expected = unpack("<IQ", body)
                if dtid != tid or expected > MAX_DATA:
                    raise ProtocolError(f"bad Start_Data (transaction {dtid}, {expected} bytes)")
                deadline = time.monotonic() + self.op_timeout
            elif kind in DATA_CARRIERS:
                (dtid,) = unpack("<I", body)
                if expected is None or dtid != tid:
                    raise ProtocolError("data outside a data phase")
                payload = len(body) - DATA_HEADER
                if len(data) + payload > expected:
                    raise ProtocolError(f"camera sent {len(data) + payload} bytes, announced {expected}")
                if payload:
                    data += body[DATA_HEADER:]
                    deadline = time.monotonic() + self.op_timeout
            elif kind == CANCEL:
                raise ProtocolError(f"camera cancelled operation 0x{code:04X}")
            elif kind == PROBE_REQUEST:
                log.debug("<- probe during 0x%04X", code)
                self.cmd.sendall(packet(PROBE_RESPONSE))
            elif kind == EVENT:
                if log.isEnabledFor(logging.DEBUG):  # Canon reports through GetEvent instead
                    log.debug("<- event during 0x%04X: %s", code, body.hex())
            else:
                raise ProtocolError(f"unexpected packet type {kind} during operation 0x{code:04X}")
