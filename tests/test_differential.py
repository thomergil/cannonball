"""Cannonball's client against the reference: byte-for-byte the same on the wire.

The reference is itu2_ptpip.py from epsimatic/canon-wifi-downloader, the
client cannonball used before it had its own (and that transferred 216
images from an EOS 90D). It is GPL-3.0, so it is not part of this repository:
it is downloaded at a pinned commit and checked against its SHA-256 first.

The same worker logic drives either client through the same fake camera; the
bytes each one sends, on both connections, have to be identical, and so do
the files saved and the images acknowledged. The parsers are compared on
random and mutated input.
"""

import hashlib
import importlib.util
import random
import socket
import struct
import sys
import urllib.request
from pathlib import Path

import pytest
from conftest import CHUNK, GUID, images
from fakecam import FakeCamera

from cannonball import canon
from cannonball.worker import Session

REF_URL = "https://raw.githubusercontent.com/epsimatic/canon-wifi-downloader/85b60cd468af20db3940dec9d1ba7d2a7a229afe/itu2_ptpip.py"
REF_SHA256 = "94f20f07a8fde3dde71e0893a6025a25a5ff5a841ea752a2ec27f061dec8f43a"


@pytest.fixture(scope="session")
def ref():
    path = Path(__file__).parent / ".reference" / "itu2_ptpip.py"  # gitignored
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != REF_SHA256:
        try:
            data = urllib.request.urlopen(REF_URL, timeout=30).read()
        except OSError as e:
            pytest.skip(f"cannot download the reference client: {e}")
        assert hashlib.sha256(data).hexdigest() == REF_SHA256, "reference client does not match its pinned hash"
        path.write_bytes(data)
    spec = importlib.util.spec_from_file_location("itu2_ptpip_reference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look themselves up there
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def ref_client(ref):
    """The reference behind cannonball's Client interface, used as cannonball used it."""

    class Reference:
        def __init__(self, host, guid, name, *, port=None, connect_timeout=3.0, op_timeout=10.0):
            self.c = ref.PTPIPClient(host, guid.hex, name, log=lambda *a: None, timeout=connect_timeout)
            self.op_timeout = op_timeout

            def connect_tcp(c=self.c):  # the reference's own, but on the fake camera's port
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.settimeout(c.timeout)
                sock.connect((c.ip, port))
                return sock

            self.c._connect_tcp = connect_tcp

        @property
        def camera_name(self):
            return self.c.camera_name

        def connect(self):
            self.c.connect()
            self.c.cmd.settimeout(self.op_timeout)

        def op(self, code, *params):
            return self.c.op(code, params)

        def close(self):
            self.c.close()

    return Reference


def session(tmp_path, client_class, cam_args, sessions=1, dedupe=False):
    """Run sessions against a fresh fake camera; return what the camera saw and what was saved."""
    imgs = images(*cam_args.pop("sizes"), seed=7)
    with FakeCamera(GUID, imgs, **cam_args) as cam:
        from conftest import make_config

        cfg = make_config(tmp_path, cam.port)
        if dedupe:
            first = imgs[0]
            path = cfg.download.destination / f"{first.time1[:4]}_{first.time1[4:6]}_{first.time1[6:8]}" / first.name
            path.parent.mkdir(parents=True)
            path.write_bytes(first.data)
        results = []
        old = Session.client_class
        Session.client_class = client_class
        try:
            for _ in range(sessions):
                try:
                    results.append(Session(cfg, "127.0.0.1", lambda: None, lambda **e: None).run())
                except Exception as e:  # noqa: BLE001 - compared below
                    results.append(type(e).__name__)
        finally:
            Session.client_class = old
    files = {str(p.relative_to(cfg.download.destination)): p.read_bytes() for p in cfg.download.destination.rglob("*") if p.is_file() and p.suffix != ".part"}
    return {
        "cmd": bytes(cam.cmd_stream),
        "event": bytes(cam.event_stream),
        "acked": cam.acked,
        "files": files,
        "results": results,
        "errors": cam.errors,
    }


SCENARIOS = {
    "plain": dict(sizes=[1, CHUNK - 1, CHUNK, CHUNK + 1, 4 * CHUNK + 17]),
    "small packets": dict(sizes=[3 * CHUNK + 5], packet_size=100),
    "one big packet": dict(sizes=[2 * CHUNK + 5], packet_size=10 * CHUNK),
    "fragmented": dict(sizes=[2 * CHUNK + 5, 9], fragment=True, packet_size=1500),
    "events on command channel": dict(sizes=[CHUNK + 5, 9], interleave_events=True),
    "short reads": dict(sizes=[3 * CHUNK + 5], short_reads=True),
    "no images": dict(sizes=[]),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_same_bytes(tmp_path, ref_client, name):
    from cannonball.ptpip import Client

    ours = session(tmp_path / "ours", Client, dict(SCENARIOS[name]))
    theirs = session(tmp_path / "theirs", ref_client, dict(SCENARIOS[name]))
    assert ours["errors"] == theirs["errors"] == []
    assert ours["results"] == theirs["results"] == [0]
    assert ours["acked"] == theirs["acked"]
    assert ours["files"] == theirs["files"]
    assert ours["event"] == theirs["event"]
    assert ours["cmd"] == theirs["cmd"]  # every byte of every request
    assert len(ours["files"]) == len(SCENARIOS[name]["sizes"])


@pytest.mark.parametrize("drop_after", [0, 1, CHUNK, 3 * CHUNK + 1])
def test_same_bytes_across_a_dropped_session(tmp_path, ref_client, drop_after):
    from cannonball.ptpip import Client

    args = dict(sizes=[4 * CHUNK + 3, 500], drop_after=drop_after)
    ours = session(tmp_path / "ours", Client, dict(args), sessions=2)
    theirs = session(tmp_path / "theirs", ref_client, dict(args), sessions=2)
    assert ours["results"][1] == theirs["results"][1] == 0
    assert ours["acked"] == theirs["acked"]
    assert ours["files"] == theirs["files"]
    assert ours["cmd"] == theirs["cmd"]


def test_same_bytes_when_already_saved(tmp_path, ref_client):
    from cannonball.ptpip import Client

    args = dict(sizes=[5000, 6000])
    ours = session(tmp_path / "ours", Client, dict(args), dedupe=True)
    theirs = session(tmp_path / "theirs", ref_client, dict(args), dedupe=True)
    assert ours["cmd"] == theirs["cmd"]
    assert ours["acked"] == theirs["acked"]


# -- parsers ---------------------------------------------------------------


def random_event_data(rng: random.Random) -> bytes:
    out = b""
    for _ in range(rng.randint(0, 6)):
        kind = rng.choice([0xC1AB, 0xC189, 0xC1A4, 0, rng.getrandbits(32)])
        if kind == 0xC1AB and rng.random() < 0.8:
            path = "".join(rng.choice("ABC:/_.0123456789\x80\xff") for _ in range(rng.randint(0, 40)))
            body = struct.pack("<6I", *(rng.getrandbits(32) for _ in range(6))) + path.encode("latin-1")
            if rng.random() < 0.7:
                body += b"\0" + rng.randbytes(rng.randint(0, 5))
        else:
            body = rng.randbytes(rng.randint(0, 40))
        length = 8 + len(body)
        if rng.random() < 0.1:
            length = rng.choice([0, 7, length + 1, rng.getrandbits(32)])
        out += struct.pack("<II", length, kind) + body
    if rng.random() < 0.2:
        out = out[: rng.randint(0, len(out))]
    return out


def random_object_info(rng: random.Random) -> bytes:
    def p16(units, text):
        return bytes([units]) + text.encode("utf-16le", "surrogatepass")

    data = rng.randbytes(0x38)
    for _ in range(rng.randint(0, 4)):
        text = "".join(chr(rng.choice([rng.randint(32, 126), rng.randint(0x80, 0xFFFF)])) for _ in range(rng.randint(0, 20)))
        units = len(text) + 1 if rng.random() < 0.8 else rng.randint(0, 255)
        data += p16(units, text + "\0")
    if rng.random() < 0.3:
        data = data[: rng.randint(0, len(data))]
    if rng.random() < 0.3:
        data += rng.randbytes(rng.randint(0, 30))
    return data


def test_same_parsing(ref):
    rng = random.Random(1)
    P = ref.PTPIPClient
    for _ in range(20000):
        data = random_event_data(rng)
        assert canon.events(data) == P.parse_events(data)
        ours = [(o.handle, o.size, o.path, o.format) for o in canon.objects(data)]
        theirs = [(o.handle, o.size, o.path, o.format) for o in P.parse_objects(P.parse_events(data))]
        assert ours == theirs, data.hex()

        data = random_object_info(rng)
        a, b = canon.object_info(data), P.parse_object_info(data)
        assert (a is None) == (b is None), data.hex()
        if a:
            assert (a.filename, a.size, a.format, a.time1, a.time2) == (b.filename, b.size, b.format, b.time1, b.time2), data.hex()
