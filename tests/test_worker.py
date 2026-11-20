"""A camera session end to end against the fake camera, including failures.

The rule under test throughout: an image is acknowledged to the camera only
after it is completely on disk, and anything that goes wrong before that
leaves it on the camera's list and no partial file under its name.
"""

import os
import time
from datetime import datetime

import pytest
from conftest import CHUNK, GUID, images
from fakecam import FakeCamera, Image

from cannonball import canon, worker
from cannonball.worker import Session


def run(cfg, *, dry_run=False):
    events = []
    code = Session(cfg, "127.0.0.1", lambda: None, lambda **e: events.append(e), dry_run).run()
    return code, events


def saved(cfg):
    return sorted(p for p in cfg.download.destination.rglob("*") if p.is_file())


def expected_path(cfg, img: Image):
    return cfg.download.destination / datetime.strptime(img.time1[:15], "%Y%m%dT%H%M%S").strftime("%Y_%m_%d") / img.name


def acks_only_when_saved(cam, cfg):
    """Check, at the moment of each acknowledgement, that the file is complete."""

    def check(img):
        path = expected_path(cfg, img)
        if not path.exists() or path.read_bytes() != img.data:
            cam.errors.append(f"{img.name} acknowledged before it was saved")
        if path.with_name(path.name + ".part").exists():
            cam.errors.append(f"{img.name} acknowledged with a .part file around")

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
    assert not list(cfg.download.destination.rglob("*.part"))
    # Canon's sequence: session setup, then per image, then the finish.
    codes = [code for code, _ in cam.ops]
    assert cam.ops[0] == (canon.OPEN_SESSION, (canon.SESSION_ID,))
    assert cam.ops[1 : 1 + len(canon.SETUP)] == canon.SETUP
    assert cam.ops[-len(canon.FINISH) :] == canon.FINISH
    assert codes.count(0x913F) == 1  # only before the first read
    assert [e["event"] for e in events].count("saved") == len(imgs)
    assert events[-1]["event"] == "finished" and events[-1]["count"] == len(imgs)


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
        with pytest.raises(Exception):
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
    assert not list(cfg.download.destination.rglob("*.part"))


@pytest.mark.parametrize("op", [canon.OBJECT_INFO, canon.READ, canon.TRANSFER_COMPLETE, canon.GET_EVENT])
def test_camera_stops_answering(cfg_for, op):
    imgs = images(2 * CHUNK)
    nth = 2 if op == canon.GET_EVENT else 1  # the first GetEvent is part of setup
    with FakeCamera(GUID, imgs, hang_on=(op, nth)) as cam:
        cfg = cfg_for(cam.port, **{"timing.op_timeout": 0.5})
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
    assert saved(cfg) == [] or all(p.suffix == ".part" for p in saved(cfg))


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


def test_stale_part_file_is_replaced(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        path = expected_path(cfg, imgs[0])
        path.parent.mkdir(parents=True)
        path.with_name(path.name + ".part").write_bytes(b"x" * 99999)
        run(cfg)
    assert path.read_bytes() == imgs[0].data
    assert not path.with_name(path.name + ".part").exists()


def test_dry_run_downloads_and_acknowledges_nothing(cfg_for):
    imgs = images(5000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port)
        run(cfg, dry_run=True)
    assert cam.acked == []
    assert saved(cfg) == []


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
