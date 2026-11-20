"""The PTP/IP client on its own: handshake, framing, and what a broken peer can do to it."""

import socket
import struct
import threading
import time

import pytest
from conftest import GUID, images
from fakecam import CAMERA_GUID, FakeCamera, pkt

from cannonball import canon, ptpip
from cannonball.ptpip import Client, ProtocolError


def client(port, **kw):
    kw.setdefault("op_timeout", 2.0)
    return Client("127.0.0.1", GUID, "testhost", port=port, **kw)


def test_handshake():
    with FakeCamera(GUID, [], name="EOS90D_FBD69F") as cam:
        c = client(cam.port)
        c.connect()
        assert c.camera_name == "EOS90D_FBD69F"
        assert c.camera_guid == CAMERA_GUID
        c.close()
    # Init_Command_Request: GUID, name in UTF-16LE with NUL, version 1.0.
    body = GUID.bytes + "testhost\0".encode("utf-16le") + struct.pack("<I", 0x00010000)
    assert bytes(cam.cmd_stream[: 8 + len(body)]) == pkt(1, body)
    assert bytes(cam.event_stream[:12]) == pkt(3, struct.pack("<I", 1))


def test_operation_encoding_and_data():
    with FakeCamera(GUID, images(10)) as cam:
        c = client(cam.port)
        c.connect()
        rc, params, data = c.op(canon.OPEN_SESSION, canon.SESSION_ID)
        assert (rc, params, data) == (0x2001, [], b"")
        rc, params, data = c.op(0x913F, 0x01000000, 0x110, 0x31000000)
        assert params == [0x80000436, 1]
        rc, _, data = c.op(canon.OBJECT_INFO, canon.ROOT)
        assert len(data) == 150
        c.close()
    assert cam.errors == []  # transaction ids 1, 2, 3; data phase 1
    start = cam.cmd_stream.index(pkt(6, struct.pack("<IHII", 1, 0x1002, 1, 0x41)))
    assert start > 0


def test_error_code_is_returned_not_raised():
    with FakeCamera(GUID, [], fail_on={0x913D: 0x2019}) as cam:
        c = client(cam.port)
        c.connect()
        assert c.op(0x913D, 0xFFF)[0] == 0x2019
        assert c.op(0x9101)[0] == 0x2001  # and the connection still works
        c.close()


def test_init_fail():
    with FakeCamera(GUID.__class__(int=1), []) as cam:
        c = client(cam.port)
        with pytest.raises(ProtocolError, match="Init_Fail"):
            c.connect()
        c.close()


def test_connection_refused():
    with FakeCamera(GUID, []) as cam:
        port = cam.port
    with pytest.raises(OSError):
        client(port).connect()


def test_timeout_when_camera_stops_answering():
    with FakeCamera(GUID, [], hang_on=(0x9101, 1)) as cam:
        c = client(cam.port, op_timeout=0.3)
        c.connect()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            c.op(0x9101)
        assert time.monotonic() - started < 1.5
        c.close()


def test_drop_mid_data():
    imgs = images(100_000)
    with FakeCamera(GUID, imgs, drop_after=5000) as cam:
        c = client(cam.port)
        c.connect()
        c.op(canon.OPEN_SESSION, canon.SESSION_ID)
        c.op(canon.GET_EVENT)  # offers the image
        with pytest.raises(ProtocolError, match="closed"):
            c.op(canon.READ, imgs[0].handle, 0, 100_000, 0, 0)
        c.close()


@pytest.mark.parametrize(
    "raw, message",
    [
        (struct.pack("<II", 4, 7), "length"),  # shorter than its header
        (struct.pack("<II", ptpip.MAX_PACKET + 1, 10), "length"),  # absurdly long
        (pkt(11, struct.pack("<I", 1)), "cancelled"),  # Cancel
        (pkt(10, struct.pack("<I", 1) + b"x"), "outside a data phase"),
        (pkt(9, struct.pack("<IQ", 1, 4)) + pkt(12, struct.pack("<I", 1) + b"toolong"), "announced"),
        (pkt(9, struct.pack("<IQ", 99, 4)), "Start_Data"),  # wrong transaction
        (pkt(9, struct.pack("<IQ", 1, ptpip.MAX_PACKET + 1)), "Start_Data"),
        (pkt(7, struct.pack("<HI", 0x2001, 99)), "transaction"),
        (pkt(2, b""), "unexpected packet type"),
    ],
)
def test_malformed_replies(raw, message):
    with FakeCamera(GUID, [], raw_on={0x9101: raw}) as cam:
        c = client(cam.port, op_timeout=1.0)
        c.connect()
        with pytest.raises(ProtocolError, match=message):
            c.op(0x9101)
        c.close()


def test_probes_are_answered_on_both_channels():
    with FakeCamera(GUID, [], probe=True) as cam:
        c = client(cam.port)
        c.connect()
        assert c.op(0x9101)[0] == 0x2001
        deadline = time.monotonic() + 2
        while cam.probes_answered < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        c.close()
    assert cam.probes_answered == 2


def test_fragmented_and_interleaved():
    imgs = images(200_000)
    with FakeCamera(GUID, imgs, fragment=True, interleave_events=True, packet_size=999) as cam:
        c = client(cam.port)
        c.connect()
        c.op(canon.OPEN_SESSION, canon.SESSION_ID)
        c.op(canon.GET_EVENT)
        rc, params, data = c.op(canon.READ, imgs[0].handle, 0, 200_000, 0, 0)
        c.close()
    assert data == imgs[0].data
    assert params == [200_000, 0, 0]


def test_close_stops_event_thread():
    with FakeCamera(GUID, []) as cam:
        before = threading.active_count()
        c = client(cam.port)
        c.connect()
        c.close()
        c.close()  # twice is fine
        deadline = time.monotonic() + 2
        while threading.active_count() > before and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(t.name == "ptpip-events" for t in threading.enumerate())


def test_event_ack_must_follow():
    """A camera that answers the event connection with anything but Init_Event_Ack is refused."""
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        cmd, _ = server.accept()
        cmd.recv(4096)
        cmd.sendall(pkt(2, struct.pack("<I", 1) + bytes(16) + "x\0".encode("utf-16le") + bytes(4)))
        ev, _ = server.accept()
        ev.recv(4096)
        ev.sendall(pkt(5, struct.pack("<I", 1)))
        time.sleep(0.5)

    threading.Thread(target=serve, daemon=True).start()
    c = client(port)
    with pytest.raises(ProtocolError, match="Init_Event_Ack"):
        c.connect()
    c.close()
    server.close()
