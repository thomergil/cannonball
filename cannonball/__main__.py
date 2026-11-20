"""cannonball: receive Canon EOS "auto send images to computer" transfers.

The supervisor (this module) never talks PTP itself. It keeps this computer
announced, watches for the camera on the network, and runs each camera
session in a child process (worker.py) that it kills and retries if it stops
making progress.
"""

import argparse
import json
import logging
import logging.handlers
import multiprocessing as mp
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, consts, discovery, worker
from .status import State, Status

log = logging.getLogger(consts.PACKAGE)

PING = "ping"
PING_TIMEOUT_FLAG = "-t" if sys.platform == "darwin" else "-W"  # seconds, on both


def ping(host: str, timeout: float) -> bool:
    args = [PING, "-c", "1", PING_TIMEOUT_FLAG, str(max(1, round(timeout))), host]
    try:
        result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 1)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class Hosts:
    """Camera addresses on our networks: configured, found, and remembered from earlier runs."""

    def __init__(self, cfg: config.Config):
        self.networks = cfg.networks
        self.path = cfg.state_dir / consts.STATE_HOSTS
        try:
            known = json.loads(self.path.read_text())
        except (OSError, ValueError):
            known = []
        self._hosts: dict[str, None] = {}
        self._lock = threading.Lock()
        for host in cfg.hosts + known:
            self._accept(host)

    def _accept(self, host: str) -> bool:
        if host in self._hosts:
            return False
        if host not in self.networks:
            log.warning("ignoring camera address %s: outside the configured networks", host)
            return False
        self._hosts[host] = None
        return True

    def add(self, host: str) -> bool:
        with self._lock:
            if not self._accept(host):
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(list(self._hosts)))
            return True

    def __iter__(self):
        with self._lock:
            return iter(list(self._hosts))


class Supervisor:
    def __init__(self, cfg: config.Config, log_queue, once: bool):
        self.cfg = cfg
        self.log_queue = log_queue
        self.once = once
        self.stop = threading.Event()
        self.hosts = Hosts(cfg)
        self.seen = threading.Event()  # set when a camera's record is found
        self.ctx = mp.get_context("spawn")
        self.proc = None
        self.proc_host = ""
        self.heartbeat = self.ctx.Value("d", 0.0)
        self.up: dict[str, bool] = {}
        self.misses: dict[str, int] = {}
        self.next_try: dict[str, float] = {}
        self.last_announce = 0.0
        self.discovery: discovery.Discovery | None = None
        self.events = self.ctx.Queue()  # progress events from workers
        self.status = Status()

    def on_camera(self, host: str, txt: dict) -> None:
        if self.hosts.add(host):
            log.info("found %s at %s", txt.get(consts.CAMERA_TXT_MODEL, "camera"), host)
        self.seen.set()

    def announce(self) -> None:
        if self.discovery and self.cfg.advertise:
            self.discovery.announce()
            self.last_announce = time.time()

    # -- worker management ----------------------------------------------

    def start_worker(self, host: str) -> None:
        self.heartbeat.value = time.time()
        self.proc = self.ctx.Process(
            target=worker.main,
            args=(self.cfg, host, self.heartbeat, self.log_queue, self.events),
            name=f"{consts.PACKAGE}-worker-{host}",
            daemon=True,
        )
        self.proc.start()
        self.proc_host = host
        log.debug("%s: worker %d started", host, self.proc.pid)

    def kill_worker(self) -> None:
        self.proc.kill()
        self.proc.join(consts.SUBPROCESS_STOP_TIMEOUT)
        self.proc = None

    def check_worker(self) -> bool:
        """Reap or kill the worker. Returns True when a sync completed and --once is set."""
        if self.proc is None:
            return False
        host = self.proc_host
        timing = self.cfg.timing
        if self.proc.is_alive():
            idle = time.time() - self.heartbeat.value
            if idle > timing.stall_timeout:
                log.warning("%s: no progress for %.0fs, killing session", host, idle)
                self.kill_worker()
                self.next_try[host] = time.time() + timing.retry_delay
            return False

        code = self.proc.exitcode
        self.proc.join()
        self.proc = None
        if code == worker.OK:
            self.next_try[host] = time.time() + timing.resync_after
            return self.once
        if code != worker.NO_CAMERA:
            log.info("%s: session ended (exit %s); retrying in %.0fs", host, code, timing.retry_delay)
        self.next_try[host] = time.time() + timing.retry_delay
        return False

    # -- main loop -------------------------------------------------------

    def drain_events(self) -> None:
        while True:
            try:
                self.status.event(self.events.get_nowait())
            except Exception:  # queue.Empty, or a half-written event from a killed worker
                return

    def check_host(self, host: str, busy: bool) -> bool:
        """Follow one camera address; start a session when it is up. Returns busy."""
        was_up = self.up.get(host, False)
        if ping(host, self.cfg.timing.ping_timeout):
            self.misses[host] = 0
            is_up = True
        else:
            # Camera Wi-Fi drops single pings; only call it gone after a few misses.
            self.misses[host] = self.misses.get(host, 0) + 1
            is_up = was_up and self.misses[host] < self.cfg.ping_misses
        self.up[host] = is_up
        if is_up and not was_up:
            log.info("%s: camera is on the network", host)
            if not busy:
                self.announce()  # make sure the freshly woken camera hears us
        elif was_up and not is_up:
            log.info("%s: camera left the network", host)
        if is_up and not busy and time.time() >= self.next_try.get(host, 0):
            self.start_worker(host)
            return True
        return busy

    def tick(self) -> bool:
        self.drain_events()
        if self.check_worker():
            return True
        self.drain_events()
        if self.discovery and not self.discovery.alive():
            log.debug("restarting mDNS announcement and browsing")
            self.announce()

        busy = self.proc is not None
        if not busy and time.time() - self.last_announce > self.cfg.timing.announce_interval:
            self.announce()
        for host in self.hosts:
            busy = self.check_host(host, busy)
        if not busy:
            self.status.set(state=State.CAMERA if any(self.up.values()) else State.WAITING, file=None)
        elif self.status.data["state"] != State.RECEIVING:
            self.status.set(state=State.CAMERA)
        return False

    def run(self) -> None:
        if not shutil.which(PING):
            log.error("no ping command: cannot see the camera come online (on Linux, install iputils-ping)")
        self.discovery = discovery.create(self.cfg, self.on_camera)
        self.discovery.start()
        self.announce()
        log.info(
            "%s running: GUID %s, saving to %s, networks %s, known cameras: %s",
            consts.PACKAGE,
            self.cfg.guid,
            self.cfg.download.destination,
            ", ".join(str(n) for n in self.cfg.networks.nets),
            ", ".join(self.hosts) or "none yet",
        )
        try:
            while not self.stop.is_set():
                try:
                    if self.tick():
                        break
                except Exception:  # noqa: BLE001 - the supervisor must never die
                    log.exception("supervisor error; continuing")
                self.seen.wait(self.cfg.timing.poll_interval)
                self.seen.clear()
        finally:
            if self.proc and self.proc.is_alive():
                self.proc.kill()
            self.discovery.close()


def setup_logging(cfg: config.Config, verbose: bool):
    """Log to stderr and a rotating file; workers log through a queue to here."""
    fmt = logging.Formatter(consts.LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.log_file:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=consts.LOG_MAX_BYTES, backupCount=consts.LOG_BACKUPS)
        )
    for h in handlers:
        h.setFormatter(fmt)
    level = logging.DEBUG if verbose else cfg.log_level
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)
    cfg.log_level = logging.getLevelName(level)

    queue = mp.get_context("spawn").Queue()
    listener = logging.handlers.QueueListener(queue, *handlers, respect_handler_level=True)
    listener.start()
    return queue, listener


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=consts.PACKAGE, description="Receive Canon EOS Wi-Fi auto-send transfers.")
    ap.add_argument("-c", "--config", type=Path, help=f"config file (default {config.DEFAULT_PATH})")
    ap.add_argument("--once", action="store_true", help="exit after the first completed sync")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args, _ = ap.parse_known_args(argv)  # Finder may add its own arguments
    return args


def supervisor(args: argparse.Namespace) -> tuple[Supervisor, logging.handlers.QueueListener]:
    cfg = config.load(args.config)
    queue, listener = setup_logging(cfg, args.verbose)
    log.debug("config: %s", cfg)
    return Supervisor(cfg, queue, args.once), listener


def main() -> None:
    sup, listener = supervisor(arguments())
    signal.signal(signal.SIGTERM, lambda *_: sup.stop.set())
    try:
        sup.run()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
