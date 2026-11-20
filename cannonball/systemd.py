"""Run Cannonball at login on Linux, as a systemd user service."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import consts

UNIT = Path(__file__).with_name(f"{consts.PACKAGE}.service")


def unit_path() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "systemd" / "user" / UNIT.name


def install() -> None:
    """Write the unit for this Python, enable it, and (re)start Cannonball."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise SystemExit("cannonball: --install-service needs systemd (systemctl not found)")
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIT.read_text().replace("@PYTHON@", sys.executable))
    for args in (["daemon-reload"], ["enable", UNIT.name], ["restart", UNIT.name]):
        done = subprocess.run([systemctl, "--user", *args], capture_output=True, text=True)
        if done.returncode:
            raise SystemExit(f"cannonball: systemctl --user {' '.join(args)}: {done.stderr.strip()}")
    print(f"{consts.APP_NAME} is running, and starts at login ({path}).")
