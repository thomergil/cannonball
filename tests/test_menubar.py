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


def opacity(image) -> float:
    """Total alpha of the rendered image."""
    from AppKit import NSBitmapImageRep

    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    return sum(rep.colorAtX_y_(x, y).alphaComponent() for x in range(rep.pixelsWide()) for y in range(rep.pixelsHigh()))


def test_shimmer_keeps_the_size_and_sweeps():
    from cannonball.menubar import CAMERA_ICON, SHIMMER_FRAMES, shimmer, shimmer_frames, symbol

    camera = symbol(CAMERA_ICON)
    frames = shimmer_frames(camera)
    sweep = frames
    assert all(f.size() == camera.size() and f.isTemplate() for f in frames)
    assert len(frames) == SHIMMER_FRAMES  # no pause between sweeps
    full = opacity(camera)
    middle = opacity(shimmer(camera, 0.5))
    assert middle < 0.9 * full  # the band fades the camera
    assert opacity(sweep[0]) > middle and opacity(sweep[-1]) > middle  # it enters and leaves at the edges
    over = [f for i, f in enumerate(sweep) if 0.2 < (i + 1) / (SHIMMER_FRAMES + 1) < 0.8]  # band center over the camera
    assert len({bytes(f.TIFFRepresentation()) for f in over}) == len(over)  # the band moves every frame
