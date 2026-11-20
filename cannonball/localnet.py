"""Make macOS ask for Local Network permission (TN3179).

macOS asks once, the first time an app performs a local network operation,
and records the answer. Connecting a UDP socket to a local network address is
such an operation and sends no traffic, so it brings up the question at a
moment the user is present: when they open the app.
"""

import logging
import socket

from . import consts

log = logging.getLogger(__name__)


def request(host: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, consts.PROBE_PORT))
        except OSError as e:
            log.debug("local network request to %s: %s", host, e)
