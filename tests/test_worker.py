"""A camera session end to end against the fake camera, including failures.

The rule under test throughout: an image is acknowledged to the camera only
after it is completely on disk, and anything that goes wrong before that
leaves it on the camera's list and no partial file under its name.
"""

import dataclasses
import os
import stat
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import CHUNK, GUID, images
from fakecam import FakeCamera, Image

from cannonball import canon, consts, worker
from cannonball.ptpip import ProtocolError
from cannonball.status import Event, Progress
from cannonball.worker import Session


def run(cfg):
    events = []
    code = Session(cfg, "127.0.0.1", lambda: None, events.append).run()
    return code, events


def saved(cfg):
    return sorted(p for p in cfg.download.destination.rglob("*") if p.is_file())


def partial(path: Path) -> Path:
    return path.with_name(consts.PARTIAL_NAME.format(name=path.name))


def expected_path(cfg, img: Image):
    return cfg.download.destination / datetime.strptime(img.time1[:15], "%Y%m%dT%H%M%S").strftime("%Y_%m_%d") / img.name


def acks_only_when_saved(cam, cfg):
    """Check, at the moment of each acknowledgement, that the file is complete."""

    def check(img):
        path = expected_path(cfg, img)
        if not path.exists() or path.read_bytes() != img.data:
            cam.errors.append(f"{img.name} acknowledged before it was saved")
        if partial(path).exists():
            cam.errors.append(f"{img.name} acknowledged with a partial file around")

    cam.on_ack = check


SIZES = [1, CHUNK - 1, CHUNK, CHUNK + 1, 5 * CHUNK + 123]


def test_full_session(cfg_for):
    imgs = images(*SIZES)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        acks_only_when_saved(cam, cfg)
        code, events = run(cfg)
    assert code == worker.OK
    assert cam.errors == []
    assert cam.acked == [img.handle for img in imgs]
    for img in imgs:
        path = expected_path(cfg, img)
        assert path.read_bytes() == img.data
        when = datetime.strptime(img.time1[:15], "%Y%m%dT%H%M%S").timestamp()
        assert os.stat(path).st_mtime == when
    assert not list(cfg.download.destination.rglob(consts.PARTIAL_NAME.format(name="*")))
    # Canon's sequence: session setup, then per image, then the finish.
    codes = [code for code, _ in cam.ops]
    assert cam.ops[0] == (canon.OPEN_SESSION, (canon.SESSION_ID,))
    assert cam.ops[1 : 1 + len(canon.SETUP)] == canon.SETUP
    assert cam.ops[-len(canon.FINISH) :] == canon.FINISH
    assert codes.count(0x913F) == 1  # only before the first read
    assert [p.event for p in events].count(Event.SAVED) == len(imgs)
    assert events[-1] == Progress(Event.FINISHED, count=len(imgs))


def test_reads_are_sequential_chunks(cfg_for):
    imgs = images(3 * CHUNK + 10)
    with FakeCamera(GUID, imgs) as cam:
        run(cfg_for(cam.port))
    reads = [p for code, p in cam.ops if code == canon.READ]
    assert reads == [(imgs[0].handle, off, min(CHUNK, len(imgs[0].data) - off), 0, 0) for off in range(0, len(imgs[0].data), CHUNK)]


def test_no_images(cfg_for):
    with FakeCamera(GUID, []) as cam:
        code, _ = run(cfg_for(cam.port))
    assert code == worker.OK
    assert cam.ops[-len(canon.FINISH) :] == canon.FINISH
    assert not any(code == canon.READ for code, _ in cam.ops)


@pytest.mark.parametrize("drop_after", [0, 1, CHUNK - 1, CHUNK, 2 * CHUNK + 5])
def test_dropped_mid_file_then_resent_from_scratch(cfg_for, drop_after):
    imgs = images(3 * CHUNK + 7, 1000)
    with FakeCamera(GUID, imgs, drop_after=drop_after) as cam:
        cfg = cfg_for(cam.port)
        acks_only_when_saved(cam, cfg)
        with pytest.raises((OSError, ProtocolError, worker.SessionError)):
            run(cfg)
        assert cam.acked == []
        assert not expected_path(cfg, imgs[0]).exists()

        first_read_of_session_two = len(cam.ops)
        code, _ = run(cfg)
    assert code == worker.OK
    assert cam.errors == []
    assert cam.acked == [img.handle for img in imgs]
    reads = [p for code, p in cam.ops[first_read_of_session_two:] if code == canon.READ]
    assert reads[0][:2] == (imgs[0].handle, 0)  # restarted from the beginning
    assert expected_path(cfg, imgs[0]).read_bytes() == imgs[0].data
    assert not list(cfg.download.destination.rglob(consts.PARTIAL_NAME.format(name="*")))


@pytest.mark.parametrize("op", [canon.OBJECT_INFO, canon.READ, canon.TRANSFER_COMPLETE, canon.GET_EVENT])
def test_camera_stops_answering(cfg_for, op):
    imgs = images(2 * CHUNK)
    nth = 2 if op == canon.GET_EVENT else 1  # the first GetEvent is part of setup
    with FakeCamera(GUID, imgs, hang_on=(op, nth)) as cam:
        cfg = cfg_for(cam.port, **{"timing.op_timeout": 0.5, "timing.read_timeout": 0.5})
        started = time.monotonic()
        with pytest.raises(OSError):  # socket timeout
            run(cfg)
        assert time.monotonic() - started < 5
        acked_first = list(cam.acked)
        code, _ = run(cfg)
    assert code == worker.OK
    if op in (canon.OBJECT_INFO, canon.READ, canon.TRANSFER_COMPLETE):
        assert acked_first == []
    assert cam.acked == [imgs[0].handle]
    assert expected_path(cfg, imgs[0]).read_bytes() == imgs[0].data


def test_first_read_may_outlast_op_timeout(cfg_for):
    """The 90D prepares the first image of a session for over 10 s before sending a byte."""
    imgs = images(2 * CHUNK, CHUNK)
    with FakeCamera(GUID, imgs, first_read_wait=1.0) as cam:
        cfg = cfg_for(cam.port, **{"timing.op_timeout": 0.5, "timing.read_timeout": 2})
        code, _ = run(cfg)
    assert code == worker.OK
    assert cam.acked == [img.handle for img in imgs]


@pytest.mark.parametrize("op", [canon.OBJECT_INFO, canon.READ, canon.TRANSFER_COMPLETE, canon.SAVE_COMPLETE, 0x9114, 0x913D])
def test_error_response_ends_the_session(cfg_for, op):
    imgs = images(1000)
    with FakeCamera(GUID, imgs, fail_on={op: 0x2002}) as cam:
        with pytest.raises(worker.SessionError):
            run(cfg_for(cam.port))
    assert cam.acked == []


def test_zero_size_is_never_saved_or_acknowledged(cfg_for):
    imgs = [Image("B:/DCIM/100CANON/IMG_0001.JPG", b"", size=0)]
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        with pytest.raises(worker.SessionError):
            run(cfg)
    assert cam.acked == []
    assert saved(cfg) == []


def test_camera_has_fewer_bytes_than_it_reported(cfg_for):
    img = images(1000)[0]
    img.size = 1500
    with FakeCamera(GUID, [img]) as cam:
        cfg = cfg_for(cam.port)
        with pytest.raises(worker.SessionError):
            run(cfg)
    assert cam.acked == []
    assert saved(cfg) == []  # nor a partial file


def test_short_reads(cfg_for):
    imgs = images(3 * CHUNK + 1)
    with FakeCamera(GUID, imgs, short_reads=True) as cam:
        cfg = cfg_for(cam.port)
        code, _ = run(cfg)
    assert code == worker.OK
    assert expected_path(cfg, imgs[0]).read_bytes() == imgs[0].data


def test_already_saved_is_acknowledged_without_download(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        path = expected_path(cfg, imgs[0])
        path.parent.mkdir(parents=True)
        path.write_bytes(imgs[0].data)
        code, _ = run(cfg)
    assert code == worker.OK
    assert cam.acked == [imgs[0].handle]
    assert not any(code == canon.READ for code, _ in cam.ops)


def test_name_collision_gets_a_suffix(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        path = expected_path(cfg, imgs[0])
        path.parent.mkdir(parents=True)
        path.write_bytes(b"another photo")
        run(cfg)
    assert path.read_bytes() == b"another photo"
    assert path.with_name(path.stem + "_1" + path.suffix).read_bytes() == imgs[0].data


def age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_abandoned_partial_file_is_replaced(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        path = expected_path(cfg, imgs[0])
        path.parent.mkdir(parents=True)
        partial(path).write_bytes(b"x" * 99999)
        age(partial(path), cfg.timing.stall_timeout + 1)
        run(cfg)
    assert path.read_bytes() == imgs[0].data
    assert not partial(path).exists()


def test_partial_file_being_written_is_left_alone(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        path = expected_path(cfg, imgs[0])
        path.parent.mkdir(parents=True)
        partial(path).write_bytes(b"another Cannonball's")
        with pytest.raises(worker.SessionError, match="another Cannonball"):
            run(cfg)
    assert cam.acked == [] and not path.exists()
    assert partial(path).read_bytes() == b"another Cannonball's"


@pytest.mark.parametrize("where, removed", [("orphan", True), ("not_ours", False), ("outside", False)])
def test_partial_file_of_a_killed_session_is_removed(cfg_for, tmp_path, where, removed):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        target = {
            "orphan": partial(cfg.download.destination / "2020_01_01" / "IMG_0001.JPG"),  # its photo is gone from the camera
            "not_ours": cfg.download.destination / "2020_01_01" / ".IMG_0001.JPG.part",
            "outside": partial(tmp_path / "elsewhere" / "IMG_0001.JPG"),
        }[where]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (cfg.state_dir / consts.STATE_PARTIAL).write_text(str(target))
        run(cfg)
    assert target.exists() != removed
    assert not (cfg.state_dir / consts.STATE_PARTIAL).exists()


def test_killed_download_is_recorded(cfg_for, monkeypatch):
    imgs = images(5000)
    recorded = []

    def killed(self, handle, size, part, name):
        recorded.append((self.cfg.state_dir / consts.STATE_PARTIAL).read_text())
        raise KeyboardInterrupt  # stands in for a kill: nothing after this line runs in a real one

    monkeypatch.setattr(Session, "receive", killed)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        with pytest.raises(KeyboardInterrupt):
            run(cfg)
    assert recorded == [str(partial(expected_path(cfg, imgs[0])))]


def test_camera_names_cannot_collide_with_partial_files(cfg_for):
    first = NamedImage("B:/DCIM/100CANON/IMG_0001.JPG", b"a" * 100, consts.PARTIAL_NAME.format(name="IMG_0002.JPG"))
    second = NamedImage("B:/DCIM/100CANON/IMG_0002.JPG", b"b" * 200, "IMG_0002.JPG")
    with FakeCamera(GUID, [first, second]) as cam:
        cfg = cfg_for(cam.port)
        code, _ = run(cfg)
    assert code == worker.OK
    assert {p.name: p.read_bytes() for p in saved(cfg)} == {"IMG_0001.JPG": first.data, "IMG_0002.JPG": second.data}



def test_wrong_guid_is_no_camera(cfg_for):
    with FakeCamera(GUID.__class__(int=GUID.int + 1), images(10)) as cam:
        cfg = cfg_for(cam.port, **{"timing.connect_window": 1})
        code, _ = run(cfg)
    assert code == worker.NO_CAMERA
    assert cam.ops == []


def test_nothing_listening_is_no_camera(cfg_for):
    with FakeCamera(GUID, []) as cam:
        port = cam.port
    cfg = cfg_for(port, **{"timing.connect_window": 1})
    started = time.monotonic()
    code, _ = run(cfg)
    assert code == worker.NO_CAMERA
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("fault", ["fragment", "interleave_events", "probe"])
def test_transport_noise(cfg_for, fault):
    imgs = images(CHUNK + 3, 77)
    with FakeCamera(GUID, imgs, packet_size=1000, **{fault: True}) as cam:
        cfg = cfg_for(cam.port)
        acks_only_when_saved(cam, cfg)
        code, _ = run(cfg)
    assert code == worker.OK
    assert cam.errors == []
    assert cam.acked == [img.handle for img in imgs]
    if fault == "probe":
        assert cam.probes_answered == 2  # one on each channel


class NamedImage(Image):
    """An image whose object info reports `name` as its file name."""

    def __init__(self, path, data, reported):
        super().__init__(path, data)
        self.reported = reported

    @property
    def name(self):
        return self.reported


@pytest.mark.parametrize(
    "reported, expected",
    [
        ("../../escaped.JPG", "escaped.JPG"),
        ("../../../../../escaped.JPG", "escaped.JPG"),
        ("{outside}/owned.JPG", "owned.JPG"),
        ("..\\..\\escaped.JPG", "escaped.JPG"),
        ("C:\\escaped.JPG", "escaped.JPG"),
        ("B:escaped.JPG", "escaped.JPG"),
        ("IMG\0.JPG", "IMG_0001.JPG"),
        (".zshenv", "IMG_0001.JPG"),
        ("-rf.JPG", "IMG_0001.JPG"),
        ("IMG_0001.terminal", "IMG_0001.JPG"),
        ("", "IMG_0001.JPG"),
        ("..", "IMG_0001.JPG"),
        ("x" * 200, "IMG_0001.JPG"),
    ],
)
def test_camera_file_names_stay_inside_the_download_folder(cfg_for, tmp_path, reported, expected):
    """A camera's file name never writes outside the download folder."""
    outside = tmp_path / "outside"
    img = NamedImage("B:/DCIM/100CANON/IMG_0001.JPG", b"x" * 100, reported.format(outside=outside))
    destination = tmp_path / "a" / "b" / "c" / "Pictures"  # room to climb out, still inside tmp_path
    with FakeCamera(GUID, [img]) as cam:
        cfg = cfg_for(cam.port, **{"download.destination": str(destination)})
        before = set(tmp_path.rglob("*"))
        code, _ = run(cfg)
    assert code == worker.OK
    new = {p for p in set(tmp_path.rglob("*")) - before if p.is_file() and not p.is_relative_to(cfg.state_dir)}
    assert [p.name for p in new] == [expected]
    assert all(p.resolve().is_relative_to(destination.resolve()) for p in new), new
    assert not outside.exists()


def test_unusable_name_falls_back_to_the_record_path(cfg_for):
    img = NamedImage("B:/DCIM/100CANON/IMG_0042.CR3", b"x" * 100, "../..")
    with FakeCamera(GUID, [img]) as cam:
        cfg = cfg_for(cam.port)
        run(cfg)
    assert [p.name for p in saved(cfg)] == ["IMG_0042.CR3"]


def test_subfolder_cannot_leave_the_download_folder(cfg_for):
    imgs = images(100)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        escaping = dataclasses.replace(cfg.download, subfolder="../{date:%Y}")  # config.load refuses this; the worker too
        cfg = dataclasses.replace(cfg, download=escaping)
        with pytest.raises(worker.SessionError, match="inside"):
            run(cfg)
    assert cam.acked == []


def test_file_and_folder_are_flushed_before_the_ack(cfg_for, monkeypatch):
    flushed = {}  # (device, inode) -> what was on disk: a file's size or a folder's names
    real = worker.flush_to_disk

    def recording(fd):
        st = os.fstat(fd)
        flushed[st.st_dev, st.st_ino] = set(os.listdir(fd)) if stat.S_ISDIR(st.st_mode) else st.st_size
        real(fd)

    monkeypatch.setattr(worker, "flush_to_disk", recording)
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)

        def check(img):
            path = expected_path(cfg, img)
            file, folder = path.stat(), path.parent.stat()
            if flushed.get((file.st_dev, file.st_ino)) != len(img.data):
                cam.errors.append(f"{path} not flushed whole before the ack")
            if path.name not in flushed.get((folder.st_dev, folder.st_ino), ()):
                cam.errors.append(f"{path.parent} not flushed after the rename")

        cam.on_ack = check
        run(cfg)
    assert cam.errors == []
    assert cam.acked == [imgs[0].handle]


@pytest.mark.parametrize("spare", [-1, 0])
def test_download_keeps_the_reserve_free(cfg_for, monkeypatch, spare):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port, **{"download.reserve_mb": 1.0})
        free = cfg.download.reserve + len(imgs[0].data) + spare
        monkeypatch.setattr(worker.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
        if spare < 0:
            with pytest.raises(worker.SessionError, match="free space"):
                run(cfg)
        else:
            assert run(cfg)[0] == worker.OK
    assert cam.acked == ([] if spare < 0 else [imgs[0].handle])
    assert len(saved(cfg)) == (0 if spare < 0 else 1)
