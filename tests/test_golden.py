"""Golden recordings: cannonball must send exactly what the stack it replaced sent.

tests/golden/*.cmd and *.event hold every byte the previous stack (worker plus
the GPL client from epsimatic/canon-wifi-downloader, as used for the first
216-image transfer from an EOS 90D) sent to the fake camera, per connection,
for each scenario below. Unlike test_differential.py these also pin the Canon
operation sequence, chunking and acknowledgement order, independently of
cannonball's own code.
"""

from pathlib import Path

import pytest
import tomli_w
from conftest import CHUNK, GUID, images
from fakecam import FakeCamera

GOLDEN = Path(__file__).parent / "golden"

SCENARIOS = {
    "three_images": dict(sizes=[1, CHUNK, 3 * CHUNK + 17]),
    "no_images": dict(sizes=[]),
    "small_packets_fragmented": dict(sizes=[2 * CHUNK + 5, 9], packet_size=1400, fragment=True),
    "events_on_command_channel": dict(sizes=[CHUNK + 5], interleave_events=True),
    "short_reads": dict(sizes=[2 * CHUNK + 1], short_reads=True),
    "drop_then_retry": dict(sizes=[3 * CHUNK + 3, 500], drop_after=CHUNK + 5, sessions=2),
    "hang_then_retry": dict(sizes=[CHUNK + 1], hang_on=(0x9174, 2), sessions=2),
    "setup_error": dict(sizes=[100], fail_on={0x913D: 0x2002}),
    "read_error": dict(sizes=[100], fail_on={0x9174: 0x2002}),
    "already_saved": dict(sizes=[5000, 6000], saved=[0]),
}


def record(config_module, worker_module, scenario: dict, tmp: Path, port: int = 0):
    """Run a scenario; return (command bytes, event bytes, acknowledged, saved files)."""
    args = dict(scenario)
    sessions = args.pop("sessions", 1)
    saved = args.pop("saved", [])
    imgs = images(*args.pop("sizes"), seed=11)
    with FakeCamera(GUID, imgs, port=port, **args) as cam:
        raw = {
            "guid": str(GUID),
            "name": "testhost",
            "advertise": False,
            "port": cam.port,
            "download": {"destination": str(tmp / "Pictures"), "chunk_size_mb": CHUNK / 1024 / 1024},
            "timing": {"connect_window": 3, "op_timeout": 0.5, "idle_timeout": 0},
            "state_dir": str(tmp / "state"),
            "log_file": "",
        }
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "config.toml").write_text(tomli_w.dumps(raw))
        cfg = config_module.load(tmp / "config.toml")
        for i in saved:
            img = imgs[i]
            path = cfg.download.destination / f"{img.time1[:4]}_{img.time1[4:6]}_{img.time1[6:8]}" / img.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(img.data)
        for _ in range(sessions):
            try:
                worker_module.Session(cfg, "127.0.0.1", lambda: None, lambda progress: None).run()
            except Exception:  # noqa: BLE001, S110 - the recording is what counts
                pass
    files = {str(p.relative_to(cfg.download.destination)): p.read_bytes() for p in cfg.download.destination.rglob("*") if p.is_file()}
    return bytes(cam.cmd_stream), bytes(cam.event_stream), cam.acked, files


@pytest.mark.parametrize("name", SCENARIOS)
def test_matches_recording(tmp_path, name):
    from cannonball import config, worker

    cmd, event, _, _ = record(config, worker, SCENARIOS[name], tmp_path)
    assert event == (GOLDEN / f"{name}.event").read_bytes()
    golden = (GOLDEN / f"{name}.cmd").read_bytes()
    if cmd != golden:
        at = next((i for i, (a, b) in enumerate(zip(cmd, golden)) if a != b), min(len(cmd), len(golden)))
        pytest.fail(f"command stream differs at byte {at} of {len(golden)}: got {cmd[at:at + 32].hex()} expected {golden[at:at + 32].hex()}")


def test_recordings_are_real():
    """Guard against an empty or truncated recording passing trivially."""
    for name in SCENARIOS:
        assert len((GOLDEN / f"{name}.cmd").read_bytes()) > 200, name
