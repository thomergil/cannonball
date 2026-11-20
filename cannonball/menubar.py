"""Menu bar icon (macOS, PyObjC).

Only reads the daemon's status file and toggles its pause flag; it never talks
to the camera, so nothing here can affect a transfer.

    python -m cannonball.menubar
"""

import argparse
import subprocess
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
)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

from . import config, consts, services
from .status import State, daemon_alive, pause_flag, read

ICONS = {  # SF Symbols
    State.WAITING: "camera",
    State.CAMERA: "camera.badge.ellipsis",
    State.RECEIVING: "arrow.down.circle.fill",
    State.PAUSED: "pause.circle",
    State.DOWN: "exclamationmark.triangle",
}
FALLBACK_ICON = ICONS[State.WAITING]

TEXT = {
    State.WAITING: "Waiting for camera",
    State.CAMERA: "Camera found, connecting…",
    State.RECEIVING: "Receiving from {camera}",
    State.PAUSED: "Paused (camera will not connect)",
    State.DOWN: f"{consts.APP_NAME} is not running",
    "starting": "Starting…",
    "camera": "camera",
    "received": "{count} received",
    "now": ", now {file}",
    "last": "Last: {count} image(s), {ago}",
    "error": "last error: {error}",
    "separator": "  ·  ",
    "start_hint": f"Start it with: python -m {consts.PACKAGE}",
    "recent": "Recent",
    "nothing": "Nothing yet",
    "recent_item": "{name}  ({ago})",
    "open_folder": "Open Pictures Folder",
    "open_log": "Open Log",
    "pause": "Pause Transfers",
    "resume": "Resume Transfers",
    "quit_icon": "Quit Menu Bar Icon",
    "quit": "Quit",
    "stop": f"Stop {consts.APP_NAME}",
    "just_now": "just now",
    "minutes": "{n} min ago",
    "hours": "{n} h ago",
    "date": "%b %-d",
}
ERROR_CHARS = 60
MINUTE, HOUR, DAY = 60, 3600, 86400
OPEN = "open"  # macOS open(1)


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


class MenuBar(NSObject):
    def initWithConfig_(self, cfg):
        self = objc.super(MenuBar, self).init()
        self.cfg = cfg
        self.icon = None
        self.recent_paths = None
        self.service_problem = services.register()

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status_line = self.add(menu, TEXT["starting"])
        self.detail_line = self.add(menu, "")
        menu.addItem_(NSMenuItem.separatorItem())
        self.recent = self.add(menu, TEXT["recent"])
        self.recent.setSubmenu_(NSMenu.alloc().init())
        self.add(menu, TEXT["open_folder"], "openFolder:")
        self.add(menu, TEXT["open_log"], "openLog:")
        menu.addItem_(NSMenuItem.separatorItem())
        self.pause = self.add(menu, TEXT["pause"], "togglePause:")
        if services.in_bundle():
            self.add(menu, TEXT["quit_icon"], "quit:")
            self.add(menu, TEXT["stop"], "stopAll:")
        else:
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

    # -- display ---------------------------------------------------------

    @objc.python_method
    def describe(self, state: State, st: dict) -> tuple[str, str]:
        """Status line and detail line for the menu."""
        title = TEXT[state].format(camera=st.get("camera") or TEXT["camera"])
        detail = ""
        if state == State.DOWN:
            detail = self.service_problem or ("" if services.in_bundle() else TEXT["start_hint"])
        elif state == State.RECEIVING:
            current = st.get("file")
            detail = TEXT["received"].format(count=st.get("received", 0)) + (TEXT["now"].format(file=current) if current else "")
        elif state == State.WAITING and st.get("last_session"):
            last = st["last_session"]
            detail = TEXT["last"].format(count=last["count"], ago=ago(last["time"]))
        if st.get("error") and state != State.RECEIVING:
            error = TEXT["error"].format(error=st["error"][:ERROR_CHARS])
            detail = detail + TEXT["separator"] + error if detail else error
        return title, detail

    def refresh_(self, _timer) -> None:
        st = read(self.cfg.state_dir)
        state = State(st.get("state", State.WAITING)) if daemon_alive(st) else State.DOWN
        if state != self.icon:
            self.item.button().setImage_(symbol(ICONS[state]))
            self.icon = state
        received = st.get("received", 0)
        self.item.button().setTitle_(f" {received}" if state == State.RECEIVING and received else "")

        title, detail = self.describe(state, st)
        self.status_line.setTitle_(title)
        self.detail_line.setTitle_(detail)
        self.detail_line.setHidden_(not detail)
        paused = pause_flag(self.cfg.state_dir).exists()
        self.pause.setTitle_(TEXT["resume"] if paused else TEXT["pause"])
        self.update_recent(st.get("recent", []))

    @objc.python_method
    def update_recent(self, recent: list[dict]) -> None:
        paths = [r["path"] for r in recent]
        if paths == self.recent_paths:
            return
        self.recent_paths = paths
        sub = self.recent.submenu()
        sub.removeAllItems()
        if not recent:
            self.add(sub, TEXT["nothing"])
        for r in recent:
            item = self.add(sub, TEXT["recent_item"].format(name=Path(r["path"]).name, ago=ago(r["time"])), "reveal:")
            item.setRepresentedObject_(r["path"])

    # -- actions ---------------------------------------------------------

    def reveal_(self, sender) -> None:
        subprocess.run([OPEN, "-R", sender.representedObject()])

    def openFolder_(self, _) -> None:
        dest = read(self.cfg.state_dir).get("destination") or str(self.cfg.download.destination)
        subprocess.run([OPEN, dest])

    def openLog_(self, _) -> None:
        if self.cfg.log_file and self.cfg.log_file.exists():
            subprocess.run([OPEN, str(self.cfg.log_file)])

    def togglePause_(self, _) -> None:
        flag = pause_flag(self.cfg.state_dir)
        if flag.exists():
            flag.unlink()
        else:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        self.refresh_(None)

    def quit_(self, _) -> None:
        NSApplication.sharedApplication().terminate_(None)

    def stopAll_(self, _) -> None:
        """Stop the daemon and remove Cannonball from login; reopen the app to undo."""
        services.unregister()
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
