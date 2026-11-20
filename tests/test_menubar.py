import sys
import time

import pytest

pytest.importorskip("AppKit")
if sys.platform != "darwin":
    pytest.skip("macOS menu bar", allow_module_level=True)

from cannonball.menubar import TEXT, describe  # noqa: E402
from cannonball.status import LastSession, Snapshot, State  # noqa: E402


def test_waiting_shows_last_session():
    title, detail = describe(State.WAITING, Snapshot(last_session=LastSession(time.time(), 3)), None)
    assert title == TEXT[State.WAITING]
    assert detail == TEXT["last"].format(count=3, ago=TEXT["just_now"])


def test_receiving_counts_the_photo_in_progress():
    title, detail = describe(State.RECEIVING, Snapshot(received=47, file="IMG_1.JPG"), None)
    assert (title, detail) == (TEXT[State.RECEIVING].format(n=48), "IMG_1.JPG")
    title, detail = describe(State.RECEIVING, Snapshot(), None)
    assert (title, detail) == (TEXT[State.RECEIVING].format(n=1), "")


def test_connecting():
    assert describe(State.CAMERA, Snapshot(), None) == (TEXT[State.CAMERA], "")


def test_error_is_shown_until_the_next_session():
    _, detail = describe(State.WAITING, Snapshot(error="session failed: x" * 20, last_session=LastSession(0, 1)), None)
    assert detail.startswith(TEXT["error"].split("{")[0])


def test_down_shows_the_service_problem():
    assert describe(State.DOWN, Snapshot(), "Allow it") == (TEXT[State.DOWN], "Allow it")
    assert describe(State.DOWN, Snapshot(), None) == (TEXT[State.DOWN], "")


def test_fan_frames_render_and_fit(monkeypatch):
    from cannonball import drawing, menubar
    from cannonball.menubar import CAMERA_ICON, FAN_RADII, symbol, with_fan

    drawn = []
    real = drawing.wifi_fan

    def recording(center, radii, line, *args):
        drawn.append((center, radii, line))
        real(center, radii, line, *args)

    monkeypatch.setattr(menubar.drawing, "wifi_fan", recording)
    camera = symbol(CAMERA_ICON)
    frames = [with_fan(camera, lit) for lit in range(1, len(FAN_RADII) + 1)]
    pictures = []
    for frame in frames:
        drawn.clear()
        pictures.append(frame.TIFFRepresentation())  # runs the drawing code
        assert drawn
        for (x, y), radii, line in drawn:
            reach = max(radii) + line / 2
            assert x + reach <= frame.size().width and y + reach <= frame.size().height  # no arc is clipped
    assert all(f.isTemplate() for f in frames) and all(pictures)
    assert len(set(bytes(p) for p in pictures)) == len(frames)  # each frame lights one more arc
