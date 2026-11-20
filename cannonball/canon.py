"""Canon EOS auto-send: the operation sequence and records ITU2 uses.

The sequence was documented by epsimatic/canon-wifi-downloader (PROTOCOL.md),
from captures of Canon Image Transfer Utility 2 talking to an EOS 250D.
"""

import struct
from dataclasses import dataclass
from datetime import datetime

RC_OK = 0x2001

OPEN_SESSION = 0x1002
SESSION_ID = 0x41
STORAGE = 0x00020001
ROOT = 0x91900000

GET_EVENT = 0x9116
OBJECT_INFO = 0x9170  # [handle]
READ = 0x9174  # [handle, offset, length, 0, 0]
STATUS = 0x9102  # [STORAGE]
PROGRESS = 0x91E9  # [handle, percent]
PROGRESS_DONE = 100
TRANSFER_COMPLETE = 0x91F0  # [handle]
SAVE_COMPLETE = 0x9177  # [handle, 0]

# After OpenSession. The data of the GetEvent in here lists pending images.
SETUP = [
    (0x9114, (SESSION_ID,)),
    (0x9115, (1,)),
    (GET_EVENT, ()),
    (OBJECT_INFO, (ROOT,)),
    (OBJECT_INFO, (ROOT,)),
    (STATUS, (STORAGE,)),
    (0x913D, (0xFFF,)),
    (0x9101, ()),
    (STATUS, (STORAGE,)),
]
# Once, before the first read of a session.
BEFORE_FIRST_READ = [
    (0x913F, (0x01000000, 0x110, 0x31000000)),
    (0x9101, ()),
]
# CompleteAutoSendImages, event mode off, remote mode off, CloseSession.
FINISH = [
    (0x916D, (0,)),
    (0x9115, (0,)),
    (0x9114, (0,)),
    (0x1003, ()),
]

OBJECT_RECORD = 0xC1AB  # an image the camera wants to send
PROPERTY_RECORDS = {0xC189, 0xC18A}  # property values and their allowed values
RECORD_LOG_BYTES = 64

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S"  # object info timestamps, e.g. 20260708T091035.0
TIMESTAMP_LENGTH = len("20260708T091035")


@dataclass(frozen=True)
class ObjectRecord:
    handle: int
    size: int
    path: str  # on the camera, e.g. B:/DCIM/100CANON/IMG_0952.JPG
    format: int = 0


@dataclass(frozen=True)
class ObjectInfo:
    filename: str
    size: int
    format: int
    time1: str = ""  # e.g. 20260708T091035.0
    time2: str = ""


def events(data: bytes) -> list[tuple[int, bytes]]:
    """Split GetEvent data into (kind, body) records: [u32 length][u32 kind][body]."""
    out = []
    pos = 0
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        if length < 8 or pos + length > len(data):
            break
        out.append((kind, data[pos + 8 : pos + length]))
        pos += length
    return out


def objects(data: bytes) -> list[ObjectRecord]:
    """Images offered in GetEvent data.

    Body: u32 handle, u32 format, u32 (0x20), u32 size, u32 (0),
    u32 thumbnail handle, NUL-terminated path.
    """
    out = []
    for kind, body in events(data):
        if kind != OBJECT_RECORD or len(body) < 24:
            continue
        handle, fmt, _, size = struct.unpack_from("<4I", body)
        path = body[24:].split(b"\0")[0].decode("latin-1")
        out.append(ObjectRecord(handle, size, path, fmt))
    return out


def _string(data: bytes, pos: int) -> tuple[str, int]:
    """A u8 length (in UTF-16 code units) followed by UTF-16LE text."""
    if pos >= len(data):
        raise ValueError("truncated")
    end = pos + 1 + 2 * data[pos]
    if end > len(data):
        raise ValueError("truncated")
    return data[pos + 1 : end].decode("utf-16le", "replace").rstrip("\0"), end


def object_info(data: bytes) -> ObjectInfo | None:
    """Decode OBJECT_INFO data: u32 storage, u32 format, u32 size, ..., strings from 0x38."""
    if len(data) < 0x39:
        return None
    _, fmt, size = struct.unpack_from("<3I", data)
    try:
        name, pos = _string(data, 0x38)
        times = []
        for _ in range(2):
            if pos < len(data):
                text, pos = _string(data, pos)
                times.append(text)
    except ValueError:
        return None
    times += [""] * (2 - len(times))
    return ObjectInfo(name, size, fmt, times[0], times[1])


def capture_time(stamp: str) -> datetime | None:
    """The camera's timestamp, without its fraction."""
    try:
        return datetime.strptime(stamp[:TIMESTAMP_LENGTH], TIMESTAMP_FORMAT)
    except ValueError:
        return None
