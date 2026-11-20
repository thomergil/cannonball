"""Menu bar icon (macOS, PyObjC), with the supervisor running on a thread.

    python -m cannonball.menubar
"""

import logging
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
    NSColor,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSWorkspace,
    NSWorkspaceOpenConfiguration,
)
from Foundation import NSURL, NSBundle, NSMakeRect, NSMakeSize, NSObject, NSTimer
from PyObjCTools import AppHelper

from . import config, consts, drawing, localnet, services
from .__main__ import Supervisor, arguments, supervisor
from .status import Snapshot, State

log = logging.getLogger(__name__)

# SF Symbols. While the camera is connected, the icon is the camera with a Wi-Fi
# fan at its top right corner that fills up arc by arc, over and over.
CAMERA_ICON = "camera"
DOWN_ICON = "exclamationmark.triangle"
CONNECTED = {State.CAMERA, State.RECEIVING}
# Fan geometry, as shares of the camera symbol's height.
CAMERA_SCALE = 0.62  # the camera, shrunk to leave room for the fan
FAN_GAP = 0.04  # from the camera's corner to the fan's center
FAN_RADII = (0.13, 0.235, 0.34)
FAN_LINE = 0.075
FAN_DOT = 0.05
FAN_SPAN = (5, 85)  # degrees: the arcs face the top right
FAN_FAINT = 0.25  # opacity of arcs not yet lit

TEXT = {
    State.WAITING: "Waiting for camera",
    State.CAMERA: "Connecting…",
    State.RECEIVING: "Transferring {n}…",
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
    "config_error_title": f"{consts.APP_NAME} cannot start",
}
ERROR_CHARS = 100
MINUTE, HOUR, DAY = 60, 3600, 86400


def symbol(name: str):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, consts.APP_NAME)
    img.setTemplate_(True)  # follows the light/dark menu bar
    return img


def with_fan(camera, lit: int):
    """The camera, made smaller, with a Wi-Fi fan of `lit` solid arcs at its top right.

    Vector drawing, so it is sharp at any screen resolution.
    """
    h = camera.size().height
    cw, ch = camera.size().width * CAMERA_SCALE, h * CAMERA_SCALE
    center = (cw + h * FAN_GAP, ch + h * FAN_GAP)  # just off the camera's corner
    radii = [r * h for r in FAN_RADII]
    line = h * FAN_LINE
    reach = radii[-1] + line / 2  # the outermost arc, stroke included

    def draw(rect) -> bool:
        camera.drawInRect_(NSMakeRect(0, 0, cw, ch))
        drawing.wifi_fan(center, radii, line, h * FAN_DOT, FAN_SPAN, NSColor.blackColor(),
                         NSColor.colorWithWhite_alpha_(0, FAN_FAINT), lit)
        return True

    size = NSMakeSize(center[0] + reach, max(h, center[1] + reach))
    img = NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    img.setTemplate_(True)
    img.setAccessibilityDescription_(consts.APP_NAME)
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


def describe(state: State, st: Snapshot, problem: str | None) -> tuple[str, str]:
    """Status line and detail line."""
    if state == State.RECEIVING:
        number = st.received + (1 if st.file else 0)  # the camera never says how many are queued
        return TEXT[state].format(n=max(number, 1)), st.file or ""
    title = TEXT[state]
    if problem or state == State.DOWN:
        return title, problem or ""
    if st.error:
        return title, TEXT["error"].format(error=st.error[:ERROR_CHARS])
    last = st.last_session
    return title, TEXT["last"].format(count=last.count, ago=ago(last.time)) if last else ""


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
        camera = symbol(CAMERA_ICON)
        self.idle, self.down = camera, symbol(DOWN_ICON)
        self.frames = [with_fan(camera, lit) for lit in range(1, len(FAN_RADII) + 1)]
        self.frame = 0
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
        st = self.sup.status.snapshot
        state = st.state if self.thread.is_alive() else State.DOWN
        if state in CONNECTED:
            moving = not NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
            self.frame = (self.frame + 1) % len(self.frames) if moving else len(self.frames) - 1
            self.item.button().setImage_(self.frames[self.frame])
        else:
            self.item.button().setImage_(self.down if state == State.DOWN else self.idle)
        title, detail = describe(state, st, self.problem or (consts.LOCAL_NETWORK_HINT if self.blocked(st) else None))
        self.status_line.setTitle_(title)
        self.detail_line.setTitle_(detail)
        self.detail_line.setHidden_(not detail)
        self.check_local_network(state, st)

    @objc.python_method
    def blocked(self, st: Snapshot) -> bool:
        """True while the system keeps us off the local network."""
        return st.refused or bool(self.sup.discovery and self.sup.discovery.denied())

    @objc.python_method
    def check_local_network(self, state: State, st: Snapshot) -> None:
        """Tell the user, once, how to allow Local Network access; macOS asks only once itself."""
        if state == State.RECEIVING:
            self.alert_flag.unlink(missing_ok=True)  # it works; a later problem is worth telling again
            return
        if self.alert_flag.exists() or not self.blocked(st) or time.monotonic() - self.started < consts.LOCAL_NETWORK_GRACE:
            return  # told already, allowed, or the user may still be answering the macOS Local Network prompt
        self.alert_flag.parent.mkdir(parents=True, exist_ok=True)
        self.alert_flag.touch()
        if alert(TEXT["blocked_title"], TEXT["blocked_body"], TEXT["open_settings"], TEXT["restart"]) == 0:
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
        opening = NSWorkspaceOpenConfiguration.configuration()
        opening.setCreatesNewApplicationInstance_(True)
        NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
            NSBundle.mainBundle().bundleURL(), opening, lambda app, error: AppHelper.callAfter(self.terminate)
        )

    def terminate(self) -> None:
        NSApplication.sharedApplication().terminate_(None)


def alert(title: str, body: str, *buttons: str) -> int:
    """Show a critical alert in front of other apps; return the index of the button clicked."""
    panel = NSAlert.alloc().init()
    panel.setAlertStyle_(NSAlertStyleCritical)
    panel.setMessageText_(title)
    panel.setInformativeText_(body)
    for button in buttons:
        panel.addButtonWithTitle_(button)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    return panel.runModal() - NSAlertFirstButtonReturn


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    try:
        sup, listener = supervisor(arguments())
    except config.ConfigError as e:  # say why, in the log and on screen, instead of quitting without a word
        try:
            config.macos_log().parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(filename=config.macos_log(), format=consts.LOG_FORMAT)
            log.error("%s", e)
        except OSError:
            pass  # the alert below still says it
        alert(TEXT["config_error_title"], str(e), TEXT["quit"])
        raise
    bar = MenuBar.alloc().initWithSupervisor_listener_(sup, listener)  # noqa: F841 - keep a reference
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
