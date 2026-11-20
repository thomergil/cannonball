import subprocess
import sys

import pytest

from cannonball import systemd


@pytest.fixture
def systemctl(tmp_path, monkeypatch):
    """A stand-in systemctl that records its calls; returns the list of calls."""
    calls = []
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(systemd.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(systemd.subprocess, "run", run)
    return calls


def test_writes_the_unit_for_this_python_and_starts_it(systemctl, tmp_path):
    systemd.install()
    unit = (tmp_path / "config/systemd/user/cannonball.service").read_text()
    assert f'ExecStart="{sys.executable}" -m cannonball' in unit
    assert "@" not in unit
    assert systemctl == [
        ["/usr/bin/systemctl", "--user", "daemon-reload"],
        ["/usr/bin/systemctl", "--user", "enable", "cannonball.service"],
        ["/usr/bin/systemctl", "--user", "restart", "cannonball.service"],
    ]


def test_a_failing_systemctl_is_one_line(systemctl, monkeypatch):
    monkeypatch.setattr(systemd.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n"))
    with pytest.raises(SystemExit, match=r"systemctl --user daemon-reload: Failed to connect to bus$"):
        systemd.install()


def test_without_systemd(systemctl, monkeypatch):
    monkeypatch.setattr(systemd.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="needs systemd"):
        systemd.install()
    assert systemctl == []
