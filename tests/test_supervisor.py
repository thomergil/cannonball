"""The daemon end to end: real supervisor, real worker processes, fake camera on 127.0.0.1."""

import threading
import time

from conftest import GUID, images
from fakecam import FakeCamera

from cannonball import canon, consts
from cannonball.__main__ import Hosts, Supervisor, setup_logging


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
    assert sup.status.snapshot.last_session.count == 3


def test_hung_worker_is_killed_and_retried(cfg_for):
    imgs = images(200_000)
    # The image's own info: the first two are part of setup.
    with FakeCamera(GUID, imgs, hang_on=(canon.OBJECT_INFO, 3)) as cam:
        # The worker itself would wait 30 s; the supervisor must not.
        cfg = cfg_for(cam.port, hosts=["127.0.0.1"], **{"timing.op_timeout": 30, "timing.read_timeout": 1, "timing.stall_timeout": 1.5, "timing.connect_timeout": 1})
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


def test_session_errors_reach_the_status(cfg_for):
    with FakeCamera(GUID, images(100), fail_on={0x913D: 0x2002}) as cam:
        cfg = cfg_for(cam.port, hosts=["127.0.0.1"])
        sup, thread, listener = start(cfg, once=False)
        deadline = time.monotonic() + 15
        while not sup.status.snapshot.error and time.monotonic() < deadline:
            time.sleep(0.1)
        stop(sup, thread, listener, timeout=0)
    assert (sup.status.snapshot.error or "").startswith("session failed: setup failed")


def test_remembered_hosts_are_capped_and_configured_ones_kept(cfg_for):
    cfg = cfg_for(hosts=["127.0.0.1"])
    hosts = Hosts(cfg)
    found = [f"127.0.1.{i}" for i in range(consts.REMEMBERED_HOSTS + 3)]
    assert all(hosts.add(h) for h in found)
    assert not hosts.add(found[-1]) and not hosts.add("127.0.0.1")  # known already
    kept = ["127.0.0.1", *found[-consts.REMEMBERED_HOSTS :]]
    assert list(hosts) == kept
    assert list(Hosts(cfg_for(hosts=["127.0.0.1"]))) == kept  # and across runs


def test_remembered_hosts_outside_the_networks_are_dropped(cfg_for):
    cfg = cfg_for(hosts=["127.0.0.1", "127.0.0.1"])
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / consts.STATE_HOSTS).write_text('["192.168.1.5", "127.0.1.1", 7]')
    assert list(Hosts(cfg)) == ["127.0.0.1", "127.0.1.1"]
