import os
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest
from conftest import GUID, make_config

from cannonball import consts, discovery


def test_computer_txt():
    txt = discovery.computer_txt("10f875fa-9e40-434f-9569-a379b3ec08fb", "studio")
    assert txt[consts.COMPUTER_TXT_GUID] == "10F875FA-9E40-434F-9569-A379B3EC08FB"
    assert txt[consts.COMPUTER_TXT_NAME] == "studio"
    assert txt["vever.canon.com"] == "0-1801-0101-01-00-00"


@pytest.fixture
def found(tmp_path):
    seen = []
    cfg = make_config(tmp_path, networks=["192.168.0.0/16"])
    return discovery.ZeroconfDiscovery(cfg, lambda address, txt: seen.append(address)), seen  # not started


def test_only_cameras_paired_with_us(found):
    d, seen = found
    d.found("192.168.0.204", {consts.CAMERA_TXT_PAIRED_GUID: str(GUID).upper()})
    d.found("192.168.0.205", {consts.CAMERA_TXT_PAIRED_GUID: str(uuid.uuid4())})
    d.found("192.168.0.206", {consts.COMPUTER_TXT_GUID: str(GUID)})  # a computer, not a camera
    assert seen == ["192.168.0.204"]


def test_only_cameras_on_our_networks(found):
    d, seen = found
    paired = {consts.CAMERA_TXT_PAIRED_GUID: str(GUID).lower()}
    d.found("96.120.65.25", paired)
    d.found("10.0.0.5", paired)  # private, but not configured here
    d.found("192.168.1.9", paired)
    assert seen == ["192.168.1.9"]


def test_avahi_parsable_output():
    line = '=;eth0;IPv4;EOS90D_FBD69F;_autosendimages._tcp;local;EOS90D.local;192.168.0.204;15740;"tid.canon.com=10F875FA" "mn.canon.com=Canon EOS 90D"\n'
    assert discovery.AvahiDiscovery.parse(line) == ("192.168.0.204", {"tid.canon.com": "10F875FA", "mn.canon.com": "Canon EOS 90D"})
    assert discovery.AvahiDiscovery.parse("+;eth0;IPv4;EOS90D_FBD69F;_autosendimages._tcp;local\n") is None
    assert discovery.AvahiDiscovery.parse(line.replace("IPv4", "IPv6")) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="mDNSResponder")
@pytest.mark.skipif(bool(os.environ.get("CI")), reason="needs mDNS on a private network; CI runners do not reliably have it")
def test_mac_round_trip(tmp_path):
    """Publish a camera-like record through mDNSResponder and find it again."""
    from cannonball import dnssd

    cfg = make_config(tmp_path, networks=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])
    interfaces = sorted(cfg.networks.interfaces())
    if not interfaces:
        pytest.skip("no interface on a private network")
    seen = threading.Event()
    addresses = []

    def on_camera(address, txt):
        addresses.append(address)
        seen.set()

    finder = discovery.MacDiscovery(cfg, on_camera)
    finder.start()
    service = dnssd.Service()
    camera = dnssd.Registration(service, f"cannonball-test-{uuid.uuid4().hex[:8]}", 15740,
                                {consts.CAMERA_TXT_PAIRED_GUID: str(GUID).upper()}, interfaces)
    camera.start()
    try:
        assert seen.wait(10), "record not found"
        assert all(a in cfg.networks for a in addresses)
        assert finder.alive()
    finally:
        camera.stop()
        service.close()
        finder.close()


class Recorder(discovery.Discovery):
    """A backend that only counts starts; healthy unless told otherwise."""

    def __init__(self, cfg):
        super().__init__(cfg, lambda address, txt: None)
        self.calls, self.healthy = [], True

    def _start(self):
        self.calls.append("start")

    def _healthy(self):
        return self.healthy

    def announce(self):
        pass

    def close(self):
        self.calls.append("close")


def test_restarts_when_the_interfaces_change(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    now = {}
    monkeypatch.setattr(type(cfg.networks), "interfaces", lambda self: dict(now))
    d = Recorder(cfg)
    d.start()  # before Wi-Fi is up
    assert d.alive()
    now[4] = "192.168.1.20"  # joined a network
    assert not d.alive()
    d.restart()
    assert d.alive() and d.calls == ["start", "close", "start"] and d.interfaces == {4: "192.168.1.20"}
    now[4] = "192.168.1.21"  # a new address on the same interface
    assert not d.alive()
    d.restart()
    d.healthy = False
    assert not d.alive()


def test_zeroconf_waits_quietly_for_an_interface(tmp_path, monkeypatch, caplog):
    cfg = make_config(tmp_path)
    monkeypatch.setattr(type(cfg.networks), "interfaces", lambda self: {})
    d = discovery.ZeroconfDiscovery(cfg, lambda address, txt: None)
    d.start()
    assert d.alive()  # nothing to restart until an interface appears
    d.announce()
    assert caplog.text.count("no network interface") == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="mDNSResponder")
def test_dnssd_service_closes_after_its_thread_failed():
    from cannonball import dnssd

    service = dnssd.Service()
    wake = (service._wake_r, service._wake_w)
    released = []
    service.lib = SimpleNamespace(DNSServiceRefDeallocate=released.append)
    held_r, held_w = os.pipe()  # stands in for an operation's socket

    def fail():
        raise RuntimeError("boom")

    service.call(lambda: service._ops.__setitem__(held_r, dnssd._Op("ref", None)))
    service.call(fail)
    assert service.failed.wait(1)
    service._thread.join(1)
    service.close()  # must not touch closed or reused descriptors
    for fd in wake:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert released == ["ref"]  # the operation the dead thread held
    os.close(held_r)
    os.close(held_w)
