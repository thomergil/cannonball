"""Menu bar icon (macOS, PyObjC).

Shows what the daemon is doing, read from its status file; it never talks to
the camera, so nothing here can affect a transfer.

    python -m cannonball.menubar
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSWorkspace,
)
from Foundation import NSURL, NSObject, NSTimer
from PyObjCTools import AppHelper

from . import config, consts, services
from .status import State, daemon_alive, read

ICONS = {  # SF Symbols
    State.WAITING: "camera",
    State.CAMERA: "camera.badge.ellipsis",
    State.RECEIVING: "arrow.down.circle.fill",
    State.DOWN: "exclamationmark.triangle",
}
FALLBACK_ICON = ICONS[State.WAITING]

TEXT = {
    State.WAITING: "Waiting for camera",
    State.CAMERA: "Camera found, connecting…",
    State.RECEIVING: "Receiving from {camera}",
    State.DOWN: f"{consts.APP_NAME} is not running",
    "camera": "camera",
    "received": "{count} received",
    "now": ", now {file}",
    "last": "Last: {count} image(s), {ago}",
    "error": "Last error: {error}",
    "open_folder": "Open Pictures Folder",
    "quit": f"Quit {consts.APP_NAME}",
    "just_now": "just now",
    "minutes": "{n} min ago",
    "hours": "{n} h ago",
    "date": "%b %-d",
}
ERROR_CHARS = 60
MINUTE, HOUR, DAY = 60, 3600, 86400


def symbol(name: str):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, consts.APP_NAME)
    if img is None:  # symbol missing on this macOS version
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(FALLBACK_ICON, consts.APP_NAME)
    img.setTemplate_(True)  # follows the light/dark menu bar
    return img


def ago(t: float) -> str:
    s = time.time() - t
    if s < MINUTE:
        return TEXT["just_now"]
    if s < HOUR:
        return TEXT["minutes"].format(n=int(s // MINUTE))
    if s < DAY:
        return TEXT["hours"].format(n=int(s // HOUR))
    return datetime.fromtimestamp(t).strftime(TEXT["date"])


def describe(state: State, st: dict, problem: str | None) -> tuple[str, str]:
    """Status line and detail line."""
    title = TEXT[state].format(camera=st.get("camera") or TEXT["camera"])
    if state == State.DOWN:
        return title, problem or ""
    if state == State.RECEIVING:
        current = st.get("file")
        return title, TEXT["received"].format(count=st.get("received", 0)) + (TEXT["now"].format(file=current) if current else "")
    if st.get("error"):
        return title, TEXT["error"].format(error=st["error"][:ERROR_CHARS])
    last = st.get("last_session")
    return title, TEXT["last"].format(count=last["count"], ago=ago(last["time"])) if last else ""


class MenuBar(NSObject):
    def initWithConfig_(self, cfg):
        self = objc.super(MenuBar, self).init()
        self.cfg = cfg
        self.icon = None
        self.problem = services.start()

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status_line = self.add(menu, "")
        self.detail_line = self.add(menu, "")
        menu.addItem_(NSMenuItem.separatorItem())
        self.add(menu, TEXT["open_folder"], "openFolder:")
        self.add(menu, TEXT["quit"], "quit:")
        self.item.setMenu_(menu)

        self.refresh_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            consts.MENU_REFRESH_SECONDS, self, "refresh:", None, True
        )
        return self

    @objc.python_method
    def add(self, menu, title, action=None):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action:
            item.setTarget_(self)
        else:
            item.setEnabled_(False)
        menu.addItem_(item)
        return item

    def refresh_(self, _timer) -> None:
        st = read(self.cfg.state_dir)
        state = State(st.get("state", State.WAITING)) if daemon_alive(st) else State.DOWN
        if state != self.icon:
            self.item.button().setImage_(symbol(ICONS[state]))
            self.icon = state
        received = st.get("received", 0)
        self.item.button().setTitle_(f" {received}" if state == State.RECEIVING and received else "")
        title, detail = describe(state, st, self.problem)
        self.status_line.setTitle_(title)
        self.detail_line.setTitle_(detail)
        self.detail_line.setHidden_(not detail)

    def openFolder_(self, _) -> None:
        folder = self.cfg.download.destination
        folder.mkdir(parents=True, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(folder)))

    def quit_(self, _) -> None:
        """Stop transfers and the icon; opening the app starts both again."""
        services.stop()
        NSApplication.sharedApplication().terminate_(None)


def main() -> None:
    ap = argparse.ArgumentParser(prog=f"{consts.PACKAGE}-menubar")
    ap.add_argument("-c", "--config", type=Path, help=f"config file (default {config.DEFAULT_PATH})")
    args, _ = ap.parse_known_args()  # Finder may add its own arguments
    cfg = config.load(args.config)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    bar = MenuBar.alloc().initWithConfig_(cfg)  # noqa: F841 - keep a reference
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
