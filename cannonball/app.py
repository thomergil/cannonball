"""Entry point of Cannonball.app on macOS.

    Cannonball          the menu bar icon (what Finder starts)
    Cannonball daemon   the transfer daemon, started and kept alive by launchd

The app registers the daemon (Contents/Library/LaunchAgents) and itself as a
login item with SMAppService, so after the first launch both run at login.
"""

import multiprocessing
import sys

from . import consts


def main() -> None:
    multiprocessing.freeze_support()  # worker processes re-enter here
    if sys.argv[1:2] == [consts.DAEMON_ARG]:
        del sys.argv[1]
        from .__main__ import main as daemon

        daemon()
    else:
        from .menubar import main as menubar

        menubar()


if __name__ == "__main__":
    main()
