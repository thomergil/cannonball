"""Background services of Cannonball.app, via SMAppService (macOS 13+).

Only meaningful inside the app bundle; from a source checkout run the daemon
yourself (`python -m cannonball`).
"""

import logging
import sys

from . import consts

log = logging.getLogger(__name__)


def in_bundle() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "darwin"


def _services():
    from ServiceManagement import SMAppService

    return [SMAppService.agentServiceWithPlistName_(consts.DAEMON_PLIST), SMAppService.mainAppService()]


def register() -> str | None:
    """Make sure the daemon and the login item are enabled. Returns a problem, if any."""
    if not in_bundle():
        return None
    from ServiceManagement import SMAppServiceStatusEnabled, SMAppServiceStatusRequiresApproval

    problem = None
    for svc in _services():
        if svc.status() == SMAppServiceStatusEnabled:
            continue
        ok, err = svc.registerAndReturnError_(None)
        if svc.status() == SMAppServiceStatusRequiresApproval:
            problem = f"Allow {consts.APP_NAME} in System Settings > General > Login Items"
        elif not ok:
            problem = f"Could not start the background service: {err.localizedDescription() if err else '?'}"
            log.error("%s", problem)
    return problem


def unregister() -> None:
    """Stop the daemon and remove both from login."""
    if not in_bundle():
        return
    for svc in _services():
        svc.unregisterAndReturnError_(None)


def open_login_items() -> None:
    from ServiceManagement import SMAppService

    SMAppService.openSystemSettingsLoginItems()
