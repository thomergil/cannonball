"""Open Cannonball.app at login, via SMAppService (macOS 13+)."""

import logging
import sys

from . import consts

log = logging.getLogger(__name__)


def in_bundle() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "darwin"


def open_at_login() -> str | None:
    """Register the app as a login item. Returns a problem to show, if any."""
    if not in_bundle():
        return None
    from ServiceManagement import SMAppService, SMAppServiceStatusEnabled, SMAppServiceStatusRequiresApproval

    item = SMAppService.mainAppService()
    if item.status() == SMAppServiceStatusEnabled:
        return None
    ok, err = item.registerAndReturnError_(None)
    if item.status() == SMAppServiceStatusRequiresApproval:
        return consts.LOGIN_ITEM_HINT
    if not ok:
        problem = consts.LOGIN_ITEM_FAILED.format(reason=err.localizedDescription() if err else "?")
        log.error("%s", problem)
        return problem
    return None
