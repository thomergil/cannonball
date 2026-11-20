import random
import sys
import uuid
from pathlib import Path

import pytest
import tomli_w

sys.path.insert(0, str(Path(__file__).parent))

from cannonball import config  # noqa: E402
from fakecam import Image  # noqa: E402

GUID = uuid.UUID("10f875fa-0000-4000-8000-00000000c0de")
CHUNK = 64 * 1024  # small chunks keep the tests fast but exercise the loop


def make_config(tmp_path: Path, port: int, **overrides) -> config.Config:
    raw = {
        "guid": str(GUID),
        "name": "testhost",
        "advertise": False,
        "hosts": [],
        "networks": ["127.0.0.0/8"],  # the fake camera is on loopback
        "port": port,
        "download": {"destination": str(tmp_path / "Pictures"), "chunk_size_mb": CHUNK / 1024 / 1024},
        "timing": {"connect_window": 3, "op_timeout": 2, "idle_timeout": 0, "retry_delay": 0.2, "resync_after": 0.2},
        "state_dir": str(tmp_path / "state"),
        "log_file": "",
    }
    for key, value in overrides.items():
        section, _, sub = key.partition(".")
        if sub:
            raw.setdefault(section, {})[sub] = value
        else:
            raw[key] = value
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.toml"
    path.write_text(tomli_w.dumps(raw))
    return config.load(path)


def images(*sizes: int, seed: int = 1) -> list[Image]:
    rng = random.Random(seed)
    return [
        Image(f"B:/DCIM/100CANON/IMG_{9000 + i:04d}.JPG", rng.randbytes(size), time1=f"2026070{1 + i % 9}T09103{i % 10}.0")
        for i, size in enumerate(sizes)
    ]


@pytest.fixture
def cfg_for(tmp_path):
    def make(port, **overrides):
        return make_config(tmp_path, port, **overrides)

    return make
