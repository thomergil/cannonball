"""Bonjour/mDNS side of Canon "auto send images to computer".

Both sides publish SERVICE_TYPE:

* The computer publishes a record whose sid.canon.com TXT entry is its
  pairing GUID. The camera opens its PTP/IP port only after it hears this
  record announced, and turns Wi-Fi off within seconds if it does not.
* The camera may publish a record whose tid.canon.com entry is the GUID of
  the computer it is paired with. It does not always do so once it has found
  the computer, so this is one source of camera addresses among others.

Announcing and browsing go through the system's mDNS service where there is
one (mDNSResponder on macOS, Avahi on Linux): on macOS the camera ignores
records answered by a second responder. python-zeroconf is the fallback.
All of it is limited to the configured networks.
"""

import logging
from abc import ABC, abstractmethod
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable

from . import consts
from .config import Config

log = logging.getLogger(__name__)

OnCamera = Callable[[str, dict[str, str]], None]


def computer_txt(guid: str, name: str) -> dict[str, str]:
    """The TXT record Canon Image Transfer Utility 2 publishes for the computer."""
    return {consts.COMPUTER_TXT_GUID: guid.upper(), consts.COMPUTER_TXT_NAME: name, **consts.COMPUTER_TXT}


class Discovery(ABC):
    """Announce this computer and report paired cameras on the configured networks."""

    def __init__(self, cfg: Config, on_camera: OnCamera):
        self.cfg = cfg
        self.guid = str(cfg.guid).upper()
        self.txt = computer_txt(str(cfg.guid), cfg.name)
        self._on_camera = on_camera

    def found(self, address: str, txt: dict[str, str]) -> None:
        """Pass on a record if it is a camera paired with us, on our networks."""
        if txt.get(consts.CAMERA_TXT_PAIRED_GUID, "").upper() != self.guid:
            return  # another computer's record, or a camera paired elsewhere
        if address not in self.cfg.networks:
            log.warning("ignoring camera at %s: outside the configured networks", address)
            return
        self._on_camera(address, txt)

    @abstractmethod
    def start(self) -> None:
        """Start looking for cameras."""

    @abstractmethod
    def announce(self) -> None:
        """(Re)publish this computer's record; the camera reacts to announcements."""

    @abstractmethod
    def withdraw(self) -> None:
        """Stop announcing."""

    @abstractmethod
    def alive(self) -> bool:
        """False when announcing or browsing has failed and needs a restart."""

    @abstractmethod
    def close(self) -> None:
        """Stop everything."""


class MacDiscovery(Discovery):
    """Through mDNSResponder's DNS-SD API."""

    def __init__(self, cfg: Config, on_camera: OnCamera):
        super().__init__(cfg, on_camera)
        from . import dnssd

        self.dnssd = dnssd
        self.service = None
        self.registration = None

    def start(self) -> None:
        interfaces = sorted(self.cfg.networks.interfaces())
        self.service = self.dnssd.Service()
        self.registration = self.dnssd.Registration(self.service, self.cfg.name, self.cfg.announce_port, self.txt, interfaces)
        self.dnssd.Browser(self.service, interfaces, self.found).start()

    def announce(self) -> None:
        if self.service is None or self.service.failed.is_set():
            self.close()
            self.start()
        self.registration.stop()
        self.registration.start()

    def withdraw(self) -> None:
        if self.registration:
            self.registration.stop()

    def alive(self) -> bool:
        return self.service is not None and not self.service.failed.is_set()

    def close(self) -> None:
        if self.service:
            self.service.close()
        self.service = self.registration = None


class AvahiDiscovery(Discovery):
    """Through the Avahi daemon's command-line tools."""

    PUBLISH = "avahi-publish-service"
    BROWSE = "avahi-browse"
    RESOLVED = "="  # first field of a resolved record in --parsable output

    @classmethod
    def usable(cls) -> bool:
        if not (shutil.which(cls.PUBLISH) and shutil.which(cls.BROWSE)):
            return False
        # Fails at once when avahi-daemon is not running.
        return subprocess.run([cls.BROWSE, "--terminate", "--parsable", consts.SERVICE_TYPE],
                              capture_output=True).returncode == 0

    def __init__(self, cfg: Config, on_camera: OnCamera):
        super().__init__(cfg, on_camera)
        self.publisher: subprocess.Popen | None = None
        self.browser: subprocess.Popen | None = None

    def start(self) -> None:
        self.browser = subprocess.Popen(
            [self.BROWSE, "--resolve", "--parsable", "--no-db-lookup", consts.SERVICE_TYPE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        threading.Thread(target=self._read, args=(self.browser,), name="avahi-browse", daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            record = self.parse(line)
            if record:
                self.found(*record)

    @classmethod
    def parse(cls, line: str) -> tuple[str, dict[str, str]] | None:
        """=;iface;IPv4;name;type;domain;host;address;port;"k=v" "k=v" -> (address, txt)."""
        fields = line.rstrip("\n").split(";", 9)
        if len(fields) < 10 or fields[0] != cls.RESOLVED or fields[2] != "IPv4":
            return None
        txt = dict(item.partition("=")[::2] for item in shlex.split(fields[9]))
        return fields[7], txt

    def announce(self) -> None:
        self.withdraw()
        args = [self.PUBLISH, self.cfg.name, consts.SERVICE_TYPE, str(self.cfg.announce_port)]
        self.publisher = subprocess.Popen(args + [f"{k}={v}" for k, v in self.txt.items()],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def withdraw(self) -> None:
        _stop(self.publisher)
        self.publisher = None

    def alive(self) -> bool:
        return all(p is None or p.poll() is None for p in (self.publisher, self.browser))

    def close(self) -> None:
        self.withdraw()
        _stop(self.browser)


class ZeroconfDiscovery(Discovery):
    """python-zeroconf's own responder, bound to the interfaces on our networks."""

    def __init__(self, cfg: Config, on_camera: OnCamera):
        super().__init__(cfg, on_camera)
        self.zc = None
        self.info = None

    def start(self) -> None:
        from zeroconf import IPVersion, ServiceBrowser, Zeroconf

        addresses = list(self.cfg.networks.interfaces().values())
        if not addresses:
            log.warning("no network interface on the configured networks")
            return
        self.zc = Zeroconf(interfaces=addresses, ip_version=IPVersion.V4Only)
        ServiceBrowser(self.zc, f"{consts.SERVICE_TYPE}.{consts.SERVICE_DOMAIN}", handlers=[self._changed])

    def _changed(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import IPVersion

        info = zeroconf.get_service_info(service_type, name, timeout=int(self.cfg.timing.connect_timeout * 1000))
        if not info:
            return
        txt = {k.decode(errors="replace"): (v or b"").decode(errors="replace") for k, v in info.properties.items()}
        for address in info.parsed_addresses(IPVersion.V4Only):
            self.found(address, txt)

    def announce(self) -> None:
        from zeroconf import ServiceInfo

        if self.zc is None:
            self.start()
            if self.zc is None:
                return
        self.withdraw()
        self.info = ServiceInfo(
            f"{consts.SERVICE_TYPE}.{consts.SERVICE_DOMAIN}",
            f"{self.cfg.name}.{consts.SERVICE_TYPE}.{consts.SERVICE_DOMAIN}",
            port=self.cfg.announce_port,
            server=f"{self.cfg.name}.{consts.SERVICE_DOMAIN}",
            parsed_addresses=list(self.cfg.networks.interfaces().values()),
            properties=self.txt,
        )
        self.zc.register_service(self.info, allow_name_change=True)

    def withdraw(self) -> None:
        if self.info and self.zc:
            self.zc.unregister_service(self.info)
        self.info = None

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        self.withdraw()
        if self.zc:
            self.zc.close()
        self.zc = None


def _stop(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(consts.SUBPROCESS_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()


def create(cfg: Config, on_camera: OnCamera) -> Discovery:
    if sys.platform == "darwin":
        return MacDiscovery(cfg, on_camera)
    if AvahiDiscovery.usable():
        return AvahiDiscovery(cfg, on_camera)
    return ZeroconfDiscovery(cfg, on_camera)
