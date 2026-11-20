"""mDNSResponder's DNS-SD C API (dns_sd.h) through ctypes, for macOS.

Announcing and browsing through the system's mDNS service means Cannonball
opens no sockets of its own for discovery. One thread owns every DNS-SD
connection: requests from other threads are queued to it, and callbacks run
on it.
"""

import ctypes
import ctypes.util
import logging
import os
import queue
import select
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import consts

log = logging.getLogger(__name__)

NO_ERROR = 0
FLAG_ADD = 0x2
PROTOCOL_IPV4 = 0x1
SOCKADDR_IN_ADDR = slice(4, 8)  # sin_addr in struct sockaddr_in, on both BSD and Linux layouts

_Ref = ctypes.c_void_p
_BrowseReply = ctypes.CFUNCTYPE(
    None, _Ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p
)
_ResolveReply = ctypes.CFUNCTYPE(
    None,
    _Ref,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_uint16,
    ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_void_p,
)
_AddrInfoReply = ctypes.CFUNCTYPE(
    None, _Ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
)
_RegisterReply = ctypes.CFUNCTYPE(
    None, _Ref, ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p
)


def _library():
    path = ctypes.util.find_library("System") or ctypes.util.find_library("dns_sd")
    lib = ctypes.CDLL(path)
    ref_out = ctypes.POINTER(_Ref)
    signatures = {
        "DNSServiceRegister": [ref_out, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                               ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p, _RegisterReply, ctypes.c_void_p],
        "DNSServiceBrowse": [ref_out, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, _BrowseReply, ctypes.c_void_p],
        "DNSServiceResolve": [ref_out, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                              _ResolveReply, ctypes.c_void_p],
        "DNSServiceGetAddrInfo": [ref_out, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, _AddrInfoReply,
                                  ctypes.c_void_p],
        "DNSServiceProcessResult": [_Ref],
    }
    for name, args in signatures.items():
        getattr(lib, name).argtypes = args
        getattr(lib, name).restype = ctypes.c_int32
    lib.DNSServiceRefSockFD.argtypes = [_Ref]
    lib.DNSServiceRefSockFD.restype = ctypes.c_int
    lib.DNSServiceRefDeallocate.argtypes = [_Ref]
    lib.DNSServiceRefDeallocate.restype = None
    return lib


def available() -> bool:
    try:
        _library()
        return True
    except (OSError, AttributeError, TypeError):
        return False


def txt_record(txt: dict[str, str]) -> bytes:
    out = b""
    for key, value in txt.items():
        item = f"{key}={value}".encode()
        out += bytes([len(item)]) + item
    return out


def parse_txt(data: bytes) -> dict[str, str]:
    out = {}
    pos = 0
    while pos < len(data):
        end = pos + 1 + data[pos]
        key, _, value = data[pos + 1 : end].decode(errors="replace").partition("=")
        out[key] = value
        pos = end
    return out


@dataclass
class _Op:
    ref: _Ref
    callback: object  # kept alive while the operation runs
    started: float = field(default_factory=time.monotonic)
    expires: bool = False


class Service:
    """A connection to mDNSResponder and the thread that serves it."""

    def __init__(self):
        self.lib = _library()
        self._ops: dict[int, _Op] = {}  # by socket fd; touched only on the service thread
        self._done: set[int] = set()  # finished inside their own callback; released after it returns
        self._requests: queue.Queue = queue.Queue()
        self._wake_r, self._wake_w = os.pipe()
        self._closed = False
        self.failed = threading.Event()  # mDNSResponder went away; re-create everything
        self._thread = threading.Thread(target=self._run, name="dnssd", daemon=True)
        self._thread.start()

    def call(self, fn: Callable[[], None]) -> None:
        """Run fn on the service thread."""
        self._requests.put(fn)
        os.write(self._wake_w, b"x")

    def close(self) -> None:
        self.call(self._close_all)
        self._thread.join(consts.DNSSD_LOOP_TICK * 2)

    # -- service thread ------------------------------------------------

    def add(self, ref: _Ref, callback, expires: bool = False) -> int:
        fd = self.lib.DNSServiceRefSockFD(ref)
        self._ops[fd] = _Op(ref, callback, expires=expires)
        return fd

    def finish(self, fd: int) -> None:
        """End an operation from within its callback."""
        self._done.add(fd)

    def drop(self, fd: int) -> None:
        op = self._ops.pop(fd, None)
        if op:
            self.lib.DNSServiceRefDeallocate(op.ref)

    def _close_all(self) -> None:
        for fd in list(self._ops):
            self.drop(fd)
        self._closed = True

    def _run(self) -> None:
        while not self._closed:
            readable, _, _ = select.select([self._wake_r, *self._ops], [], [], consts.DNSSD_LOOP_TICK)
            if self._wake_r in readable:
                os.read(self._wake_r, 4096)
                while not self._requests.empty():
                    self._requests.get()()
            for fd in readable:
                op = self._ops.get(fd)
                if op and self.lib.DNSServiceProcessResult(op.ref) != NO_ERROR:
                    log.warning("mDNSResponder connection lost")
                    self.drop(fd)
                    self.failed.set()
            for fd in self._done:
                self.drop(fd)
            self._done.clear()
            now = time.monotonic()
            for fd, op in list(self._ops.items()):
                if op.expires and now - op.started > consts.DNSSD_RESOLVE_TIMEOUT:
                    self.drop(fd)
        os.close(self._wake_r)
        os.close(self._wake_w)


class Registration:
    """This computer's record, on the given interfaces."""

    def __init__(self, service: Service, name: str, port: int, txt: dict[str, str], interfaces: list[int]):
        self.service = service
        self.args = (name.encode(), port, txt_record(txt), interfaces)
        self._fds: list[int] = []

    def start(self) -> None:
        self.service.call(self._register)

    def stop(self) -> None:
        self.service.call(self._withdraw)

    def _register(self) -> None:
        name, port, txt, interfaces = self.args

        @_RegisterReply
        def reply(ref, flags, error, *_):
            if error != NO_ERROR:
                log.warning("announcing failed: DNS-SD error %d", error)
                self.service.failed.set()

        for index in interfaces:
            ref = _Ref()
            error = self.service.lib.DNSServiceRegister(
                ctypes.byref(ref), 0, index, name, consts.SERVICE_TYPE.encode(), None, None,
                socket.htons(port), len(txt), txt, reply, None,
            )
            if error != NO_ERROR:
                log.warning("announcing on interface %d failed: DNS-SD error %d", index, error)
                self.service.failed.set()
                continue
            self._fds.append(self.service.add(ref, reply))

    def _withdraw(self) -> None:
        for fd in self._fds:
            self.service.drop(fd)
        self._fds = []


class Browser:
    """Report every record of SERVICE_TYPE as on_found(address, txt), on the given interfaces."""

    def __init__(self, service: Service, interfaces: list[int], on_found: Callable[[str, dict[str, str]], None]):
        self.service = service
        self.interfaces = interfaces
        self.on_found = on_found
        self._fds: list[int] = []

    def start(self) -> None:
        self.service.call(self._browse)

    def stop(self) -> None:
        self.service.call(self._stop)

    def _stop(self) -> None:
        for fd in self._fds:
            self.service.drop(fd)
        self._fds = []

    def _browse(self) -> None:
        @_BrowseReply
        def found(ref, flags, index, error, name, regtype, domain, context):
            if error == NO_ERROR and flags & FLAG_ADD:
                self._resolve(index, name, regtype, domain)

        for index in self.interfaces:
            ref = _Ref()
            error = self.service.lib.DNSServiceBrowse(ctypes.byref(ref), 0, index, consts.SERVICE_TYPE.encode(), None, found, None)
            if error != NO_ERROR:
                log.warning("browsing on interface %d failed: DNS-SD error %d", index, error)
                self.service.failed.set()
                continue
            self._fds.append(self.service.add(ref, found))

    def _resolve(self, index: int, name: bytes, regtype: bytes, domain: bytes) -> None:
        holder = {}

        @_ResolveReply
        def resolved(ref, flags, index, error, fullname, host, port, txt_len, txt, context):
            self.service.finish(holder["fd"])
            if error == NO_ERROR:
                self._address(index, host, parse_txt(ctypes.string_at(txt, txt_len)))

        ref = _Ref()
        if self.service.lib.DNSServiceResolve(ctypes.byref(ref), 0, index, name, regtype, domain, resolved, None) == NO_ERROR:
            holder["fd"] = self.service.add(ref, resolved, expires=True)

    def _address(self, index: int, host: bytes, txt: dict[str, str]) -> None:
        holder = {}

        @_AddrInfoReply
        def got(ref, flags, index, error, hostname, address, ttl, context):
            self.service.finish(holder["fd"])
            if error == NO_ERROR and address:
                raw = ctypes.string_at(address, SOCKADDR_IN_ADDR.stop)[SOCKADDR_IN_ADDR]
                self.on_found(socket.inet_ntoa(raw), txt)

        ref = _Ref()
        if self.service.lib.DNSServiceGetAddrInfo(ctypes.byref(ref), 0, index, PROTOCOL_IPV4, host, got, None) == NO_ERROR:
            holder["fd"] = self.service.add(ref, got, expires=True)

