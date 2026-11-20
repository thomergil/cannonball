"""The daemon end to end: real supervisor, real worker processes, fake camera on 127.0.0.1."""

import threading
import time

from conftest import GUID, images
from fakecam import FakeCamera

from cannonball import canon
from cannonball.__main__ import Supervisor, setup_logging


def start(cfg, once=True):
    queue, listener = setup_logging(cfg, verbose=False)
    sup = Supervisor(cfg, queue, once=once)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    return sup, thread, listener


def stop(sup, thread, listener, timeout=60):
    thread.join(timeout)
    finished = not thread.is_alive()
    sup.stop.set()
    thread.join(10)
    listener.stop()
    return finished


def test_daemon_receives_everything(cfg_for):
    imgs = images(300_000, 5, 70_000)
    with FakeCamera(GUID, imgs) as cam:
        cfg = cfg_for(cam.port, hosts=["127.0.0.1"])
        sup, thread, listener = start(cfg)
        assert stop(sup, thread, listener), "no complete session within 60 s"
    assert cam.acked == [img.handle for img in imgs]
    st = sup.status.data
    assert st["last_session"]["count"] == 3


def test_hung_worker_is_killed_and_retried(cfg_for):
    imgs = images(200_000)
    with FakeCamera(GUID, imgs, hang_on=(canon.READ, 1)) as cam:
        # The worker itself would wait 30 s; the supervisor must not.
        cfg = cfg_for(cam.port, hosts=["127.0.0.1"], **{"timing.op_timeout": 30, "timing.stall_timeout": 1.5})
        started = time.monotonic()
        sup, thread, listener = start(cfg)
        assert stop(sup, thread, listener)
        assert time.monotonic() - started < 20
    assert cam.sessions == 2
    assert cam.acked == [imgs[0].handle]



def test_session_failures_are_retried(cfg_for):
    imgs = images(150_000, 10)
    with FakeCamera(GUID, imgs, drop_after=100_000) as cam:
        cfg = cfg_for(cam.port, hosts=["127.0.0.1"])
        sup, thread, listener = start(cfg)
        assert stop(sup, thread, listener)
    assert cam.sessions >= 2
    assert cam.acked == [img.handle for img in imgs]
    assert "session failed" in (sup.status.data["error"] or "") or cam.sessions >= 2
