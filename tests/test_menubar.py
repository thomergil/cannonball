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


def test_receiving_shows_progress():
    title, detail = describe(State.RECEIVING, {"camera": "EOS90D", "received": 2, "file": "IMG_1.JPG"}, None)
    assert title == TEXT[State.RECEIVING].format(camera="EOS90D")
    assert detail == TEXT["received"].format(count=2) + TEXT["now"].format(file="IMG_1.JPG")


def test_error_is_shown_until_the_next_session():
    _, detail = describe(State.WAITING, {"error": "session failed: x" * 20, "last_session": {"time": 0, "count": 1}}, None)
    assert detail.startswith(TEXT["error"].split("{")[0])


def test_down_shows_the_service_problem():
    assert describe(State.DOWN, {}, "Allow it") == (TEXT[State.DOWN], "Allow it")
    assert describe(State.DOWN, {}, None) == (TEXT[State.DOWN], "")
