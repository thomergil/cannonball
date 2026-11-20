import pytest

from cannonball.status import Event, Progress, State, Status


def test_session_lifecycle():
    status = Status()
    status.event(Progress(Event.ERROR, message="session failed: x"))
    status.event(Progress(Event.REFUSED))
    assert status.snapshot.error == "session failed: x" and status.snapshot.refused

    status.event(Progress(Event.CONNECTED))
    st = status.snapshot
    assert (st.state, st.received, st.error, st.refused) == (State.RECEIVING, 0, None, False)

    status.event(Progress(Event.RECEIVING, file="IMG_1.CR3"))
    assert status.snapshot.file == "IMG_1.CR3"
    status.event(Progress(Event.SAVED))
    assert (status.snapshot.received, status.snapshot.file) == (1, None)

    status.event(Progress(Event.FINISHED, count=1))
    assert status.snapshot.last_session.count == 1

    status.event(Progress(Event.RECEIVING, file="IMG_2.CR3"))  # cut off before SAVED
    status.event(Progress(Event.CONNECTED))  # the next session counts from 0
    assert (status.snapshot.received, status.snapshot.file) == (0, None)


def test_unknown_field_is_an_error():
    with pytest.raises(TypeError):
        Status().set(recieved=1)
