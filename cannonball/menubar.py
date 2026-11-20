"""Menu bar icon (macOS, PyObjC), with the supervisor running on a thread.

    python -m cannonball.menubar
"""

import threading
import time
from datetime import datetime

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleCritical,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSWorkspace,
    NSWorkspaceOpenConfiguration,
)
from Foundation import NSURL, NSBundle, NSObject, NSTimer
from PyObjCTools import AppHelper

from . import consts, localnet, services
from .__main__ import Supervisor, arguments, supervisor
from .status import State

ICONS = {  # SF Symbols
    State.WAITING: "camera",
    State.CAMERA: "camera.badge.ellipsis",
    State.RECEIVING: "arrow.down.circle.fill",
    State.DOWN: "exclamationmark.triangle",
}
FALLBACK_ICON = ICONS[State.WAITING]

TEXT = {
    State.WAITING: "Waiting for camera",
    State.CAMERA: "Connecting…",
    State.RECEIVING: "Transferring photo {n}…",
    State.DOWN: f"{consts.APP_NAME} is not running",
    "last": "Last: {count} image(s), {ago}",
    "error": "Last error: {error}",
    "open_folder": "Open Pictures Folder",
    "quit": f"Quit {consts.APP_NAME}",
    "just_now": "just now",
    "minutes": "{n} min ago",
    "hours": "{n} h ago",
    "date": "%b %-d",
    "blocked_title": f"{consts.APP_NAME} can't reach your camera",
    "blocked_body": (
        f"macOS is blocking {consts.APP_NAME} from your local network, so photos cannot be received.\n\n"
        f"Click Open System Settings, then Local Network, and turn on {consts.APP_NAME}."
    ),
    "open_settings": "Open System Settings",
    "restart": f"Restart {consts.APP_NAME}",
}
ERROR_CHARS = 100
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
    if state == State.RECEIVING:
        current = st.get("file")
        number = st.get("received", 0) + (1 if current else 0)  # the camera never says how many are queued
        return TEXT[state].format(n=max(number, 1)), current or ""
    title = TEXT[state]
    if problem or state == State.DOWN:
        return title, problem or ""
    if st.get("error"):
        return title, TEXT["error"].format(error=st["error"][:ERROR_CHARS])
    last = st.get("last_session")
    return title, TEXT["last"].format(count=last["count"], ago=ago(last["time"])) if last else ""


class MenuBar(NSObject):
    def initWithSupervisor_listener_(self, sup: Supervisor, listener):
        self = objc.super(MenuBar, self).init()
        self.sup = sup
        self.listener = listener
        self.started = time.monotonic()
        self.alert_flag = sup.cfg.state_dir / consts.STATE_ALERTED
        hosts = list(sup.hosts)
        if hosts:
            localnet.request(hosts[0])  # macOS asks now, while the user is here
        self.thread = threading.Thread(target=sup.run, name=consts.PACKAGE, daemon=True)
        self.thread.start()
        self.icon = None
        self.problem = services.open_at_login()

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
        st = self.sup.status.data
        state = st["state"] if self.thread.is_alive() else State.DOWN
        if state != self.icon:
            self.item.button().setImage_(symbol(ICONS[state]))
            self.icon = state
        received = st.get("received", 0)
        self.item.button().setTitle_(f" {received}" if state == State.RECEIVING and received else "")
        title, detail = describe(state, st, self.problem or (consts.LOCAL_NETWORK_HINT if self.blocked() else None))
        self.status_line.setTitle_(title)
        self.detail_line.setTitle_(detail)
        self.detail_line.setHidden_(not detail)
        self.check_local_network(state)

    @objc.python_method
    def blocked(self) -> bool:
        st = self.sup.status.data
        refused = st.get("error") == consts.LOCAL_NETWORK_HINT  # the camera answered, our connections did not get out
        return refused or bool(self.sup.discovery and self.sup.discovery.denied())

    @objc.python_method
    def check_local_network(self, state: State) -> None:
        """Tell the user, once, how to allow Local Network access; macOS asks only once itself."""
        if state == State.RECEIVING:
            self.alert_flag.unlink(missing_ok=True)  # it works; a later problem is worth telling again
            return
        if self.alert_flag.exists() or not self.blocked() or time.monotonic() - self.started < consts.LOCAL_NETWORK_GRACE:
            return  # told already, allowed, or the user may still be answering macOS's own question
        self.alert_flag.parent.mkdir(parents=True, exist_ok=True)
        self.alert_flag.touch()
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleCritical)
        alert.setMessageText_(TEXT["blocked_title"])
        alert.setInformativeText_(TEXT["blocked_body"])
        alert.addButtonWithTitle_(TEXT["open_settings"])
        alert.addButtonWithTitle_(TEXT["restart"])
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() == NSAlertFirstButtonReturn:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(consts.LOCAL_NETWORK_SETTINGS))
        else:
            self.restart()

    def openFolder_(self, _) -> None:
        folder = self.sup.cfg.download.destination
        folder.mkdir(parents=True, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(folder)))

    @objc.python_method
    def stop(self) -> None:
        """Stop transfers; an image cut off is sent again next time."""
        self.sup.stop.set()
        self.thread.join(self.sup.cfg.timing.poll_interval + consts.SUBPROCESS_STOP_TIMEOUT)
        self.listener.stop()

    def quit_(self, _) -> None:
        self.stop()
        NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def restart(self) -> None:
        self.stop()
        config = NSWorkspaceOpenConfiguration.configuration()
        config.setCreatesNewApplicationInstance_(True)
        NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
            NSBundle.mainBundle().bundleURL(), config, lambda app, error: AppHelper.callAfter(self.terminate)
        )

    def terminate(self) -> None:
        NSApplication.sharedApplication().terminate_(None)


def main() -> None:
    sup, listener = supervisor(arguments())
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    bar = MenuBar.alloc().initWithSupervisor_listener_(sup, listener)  # noqa: F841 - keep a reference
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
