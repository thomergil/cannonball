import os
import sys
import threading
import uuid

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
    cfg = make_config(tmp_path, 0, networks=["192.168.0.0/16"])
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

    cfg = make_config(tmp_path, 0, networks=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])
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
