"""Background services of Cannonball.app, via SMAppService (macOS 13+).

The app registers itself as a login item and the daemon as a launch agent.
Quitting the app unregisters the daemon, which stops it; opening the app (by
hand or at login) registers it again. Only meaningful inside the app bundle;
from a source checkout run the daemon yourself (`python -m cannonball`).
"""

import logging
import sys

from . import consts

log = logging.getLogger(__name__)


def in_bundle() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "darwin"


def _daemon():
    from ServiceManagement import SMAppService

    return SMAppService.agentServiceWithPlistName_(consts.DAEMON_PLIST)


def _login_item():
    from ServiceManagement import SMAppService

    return SMAppService.mainAppService()


def start() -> str | None:
    """Run the daemon and open the app at login. Returns a problem to show, if any."""
    if not in_bundle():
        return None
    from ServiceManagement import SMAppServiceStatusEnabled, SMAppServiceStatusRequiresApproval

    problem = None
    for svc in (_daemon(), _login_item()):
        if svc.status() == SMAppServiceStatusEnabled:
            continue
        ok, err = svc.registerAndReturnError_(None)
        if svc.status() == SMAppServiceStatusRequiresApproval:
            problem = f"Allow {consts.APP_NAME} in System Settings > General > Login Items"
        elif not ok:
            problem = f"Could not start the background service: {err.localizedDescription() if err else '?'}"
            log.error("%s", problem)
    return problem


def stop() -> None:
    """Stop the daemon. The login item stays, so Cannonball starts again at login."""
    if in_bundle():
        _daemon().unregisterAndReturnError_(None)
