"""Menu bar icon (macOS, PyObjC), with the supervisor running on a thread.

    python -m cannonball.menubar
"""

import logging
import threading
import time
from datetime import datetime
from enum import StrEnum, auto

import objc
from AppKit import (
    NSAboutPanelOptionApplicationName,
    NSAboutPanelOptionApplicationVersion,
    NSAboutPanelOptionCredits,
    NSAboutPanelOptionVersion,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleCritical,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSColor,
    NSCompositingOperationDestinationOut,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSLinkAttributeName,
    NSMenu,
    NSMenuItem,
    NSSquareStatusItemLength,
    NSStatusBar,
    NSWorkspace,
    NSWorkspaceOpenConfiguration,
)
from Foundation import NSURL, NSBundle, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

from . import __version__, config, consts, localnet, services, updates
from .__main__ import Supervisor, arguments, supervisor
from .status import Snapshot, State

log = logging.getLogger(__name__)

# SF Symbol names. The shimmer is a soft band sweeping across the camera without pause.
CAMERA_ICON = "camera"
DOWN_ICON = "exclamationmark.triangle"
SHIMMER_STEP = 0.05  # seconds per frame
SHIMMER_FRAMES = 24  # one sweep
SHIMMER_WIDTH = 0.5  # of the band, as a share of the camera's width
SHIMMER_DEPTH = 0.7  # how far the band's middle fades the camera
SHIMMER_STILL = 0.5  # where the band rests when it does not move, as a share of the camera's width

TEXT = {
    State.WAITING: "Waiting for camera",
    State.CAMERA: "Connecting…",
    State.RECEIVING: "Transferring {n}…",
    State.DOWN: f"{consts.APP_NAME} is not running",
    "last": "Last: {count} image(s), {ago}",
    "error": "Last error: {error}",
    "open_folder": "Open Pictures Folder",
    "about": f"About {consts.APP_NAME}",
    "updates": "Check for Updates…",
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


def shimmer(camera, position: float):
    """The camera faded under a soft vertical band centered at `position`
    (a share of its width), at the camera's own size.

    Vector drawing, so it is sharp at any screen resolution.
    """
    size = camera.size()
    band = size.width * SHIMMER_WIDTH
    clear, faded = NSColor.colorWithWhite_alpha_(0, 0), NSColor.colorWithWhite_alpha_(0, SHIMMER_DEPTH)

    def draw(rect) -> bool:
        camera.drawInRect_(rect)
        NSGraphicsContext.currentContext().setCompositingOperation_(NSCompositingOperationDestinationOut)
        gradient = NSGradient.alloc().initWithColors_([clear, faded, clear])
        gradient.drawInRect_angle_(NSMakeRect(position * size.width - band / 2, 0, band, size.height), 0)
        return True

    img = NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    img.setAlignmentRect_(camera.alignmentRect())  # sits where the plain camera sits
    img.setTemplate_(True)
    img.setAccessibilityDescription_(consts.APP_NAME)
    return img


def shimmer_frames(camera) -> list:
    """One sweep of the band across the camera, left to right."""
    edge = SHIMMER_WIDTH / 2  # band centers beyond this miss the camera
    step = (1 + 2 * edge) / (SHIMMER_FRAMES + 1)
    return [shimmer(camera, -edge + i * step) for i in range(1, SHIMMER_FRAMES + 1)]


class Icon(StrEnum):
    IDLE = auto()  # waiting for the camera
    STILL = auto()  # camera on the network, connecting; also in place of SHIMMER under Reduce Motion
    SHIMMER = auto()  # photos arriving
    DOWN = auto()  # Cannonball not running


def icon(state: State, reduce_motion: bool) -> Icon:
    """The menu bar icon for a state: animated only while photos arrive and Reduce Motion is off."""
    if state == State.RECEIVING:
        return Icon.STILL if reduce_motion else Icon.SHIMMER
    if state == State.CAMERA:
        return Icon.STILL
    return Icon.DOWN if state == State.DOWN else Icon.IDLE


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


def about_options() -> dict:
    """The About panel's name, version and link to the project; macOS adds the app's icon.

    Name and version are given, not read from Info.plist, so a run from a checkout shows them too.
    """
    homepage = NSAttributedString.alloc().initWithString_attributes_(
        consts.HOMEPAGE, {NSLinkAttributeName: NSURL.URLWithString_(consts.HOMEPAGE)}
    )
    return {
        NSAboutPanelOptionApplicationName: consts.APP_NAME,
        NSAboutPanelOptionApplicationVersion: __version__,
        NSAboutPanelOptionVersion: "",  # else macOS adds CFBundleVersion in parentheses
        NSAboutPanelOptionCredits: homepage,
    }


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
        self.images = {Icon.IDLE: camera, Icon.STILL: shimmer(camera, SHIMMER_STILL), Icon.DOWN: symbol(DOWN_ICON)}
        self.frames = shimmer_frames(camera)
        self.frame = 0
        self.animation = None  # NSTimer that advances the shimmer frames; None unless the shimmer is shown
        self.shown = None  # Icon now in the menu bar; None before the first show()
        self.problem = services.open_at_login()

        # Square, fixed width: each image change in a variable-length item makes the menu bar
        # lay out again, which the shimmer would do 20 times a second.
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSSquareStatusItemLength)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status_line = self.add(menu, "")
        self.detail_line = self.add(menu, "")
        menu.addItem_(NSMenuItem.separatorItem())
        self.add(menu, TEXT["open_folder"], "openFolder:")
        menu.addItem_(NSMenuItem.separatorItem())
        self.add(menu, TEXT["about"], "about:")
        self.updater = updates.updater()
        if self.updater is not None:
            self.add(menu, TEXT["updates"], "checkForUpdates:").setTarget_(self.updater)
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
        self.show(icon(state, NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()))
        title, detail = describe(state, st, self.problem or (consts.LOCAL_NETWORK_HINT if self.blocked(st) else None))
        self.status_line.setTitle_(title)
        self.detail_line.setTitle_(detail)
        self.detail_line.setHidden_(not detail)
        self.check_local_network(state, st)

    @objc.python_method
    def show(self, wanted: Icon) -> None:
        """Put `wanted` in the menu bar unless it is already shown.

        Starts the frame timer for Icon.SHIMMER and stops it for any other icon.
        """
        if wanted == self.shown:
            return
        if self.animation:
            self.animation.invalidate()
            self.animation = None
        if wanted == Icon.SHIMMER:
            self.frame = 0
            self.animation = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                SHIMMER_STEP, self, "nextFrame:", None, True
            )
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.animation, NSRunLoopCommonModes)  # also while the menu is open
        else:
            self.item.button().setImage_(self.images[wanted])
        self.shown = wanted

    def nextFrame_(self, _timer) -> None:
        self.item.button().setImage_(self.frames[self.frame])
        self.frame = (self.frame + 1) % len(self.frames)

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

    def about_(self, _) -> None:
        app = NSApplication.sharedApplication()
        app.activateIgnoringOtherApps_(True)  # a menu bar app's panel opens behind the frontmost app otherwise
        app.orderFrontStandardAboutPanelWithOptions_(about_options())

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
