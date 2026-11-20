import sys
import time

import pytest

pytest.importorskip("AppKit")
if sys.platform != "darwin":
    pytest.skip("macOS menu bar", allow_module_level=True)

from cannonball.menubar import TEXT, describe  # noqa: E402
from cannonball.status import State  # noqa: E402


def test_waiting_shows_last_session():
    title, detail = describe(State.WAITING, {"last_session": {"time": time.time(), "count": 3}}, None)
    assert title == TEXT[State.WAITING]
    assert detail == TEXT["last"].format(count=3, ago=TEXT["just_now"])


def test_receiving_counts_the_photo_in_progress():
    title, detail = describe(State.RECEIVING, {"received": 47, "file": "IMG_1.JPG"}, None)
    assert (title, detail) == (TEXT[State.RECEIVING].format(n=48), "IMG_1.JPG")
    title, detail = describe(State.RECEIVING, {"received": 0, "file": None}, None)
    assert (title, detail) == (TEXT[State.RECEIVING].format(n=1), "")


def test_connecting():
    assert describe(State.CAMERA, {}, None) == (TEXT[State.CAMERA], "")


def test_error_is_shown_until_the_next_session():
    _, detail = describe(State.WAITING, {"error": "session failed: x" * 20, "last_session": {"time": 0, "count": 1}}, None)
    assert detail.startswith(TEXT["error"].split("{")[0])


def test_down_shows_the_service_problem():
    assert describe(State.DOWN, {}, "Allow it") == (TEXT[State.DOWN], "Allow it")
    assert describe(State.DOWN, {}, None) == (TEXT[State.DOWN], "")
