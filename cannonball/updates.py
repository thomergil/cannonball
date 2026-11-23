"""Software updates for the macOS app through Sparkle (sparkle-project.org).
Sparkle checks the feed in Info.plist's SUFeedURL (consts.UPDATE_FEED) by
itself, and the "Check for Updates…" menu item makes it check immediately.
Sparkle installs an update only if the update key whose public half is
SUPublicEDKey (consts.UPDATE_PUBLIC_KEY) signed it.

Only the built app carries Sparkle; updater() returns None when Cannonball
runs from a source checkout.
"""

import logging
import sys
from pathlib import Path

import objc

from . import services

log = logging.getLogger(__name__)

FRAMEWORK = "Sparkle.framework"


def updater():
    """Start Sparkle's SPUStandardUpdaterController and return it; return None
    outside the built app or when Sparkle.framework fails to load."""
    if not services.in_bundle():
        return None
    # sys.executable is Contents/MacOS/<app>; frameworks are beside MacOS
    path = Path(sys.executable).resolve().parent.parent / "Frameworks" / FRAMEWORK
    try:
        objc.loadBundle("Sparkle", {}, bundle_path=str(path))
        controller = objc.lookUpClass("SPUStandardUpdaterController")
    except (ImportError, objc.error) as e:
        log.error("updates unavailable: cannot load %s: %s", path, e)
        return None
    # No user driver delegate: for a menu bar app, Sparkle shows an update it found
    # on its own in front only soon after launch or when the Mac was idle, else
    # behind other windows. Bringing it forward would steal keyboard focus onto
    # its Install button.
    return controller.alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(True, None, None)
