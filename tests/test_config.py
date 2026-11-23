import re
import tomllib
import uuid

import pytest
import tomli_w

from cannonball import config


@pytest.fixture(autouse=True)
def no_itu2(monkeypatch):
    monkeypatch.setattr(config, "_itu2_guid", lambda: None)


def load(tmp_path, raw):
    path = tmp_path / "config.toml"
    path.write_text(tomli_w.dumps({"state_dir": str(tmp_path / "state"), **raw}))
    return config.load(path)


def defaults():
    return tomllib.loads(config.DEFAULTS.read_text())


def test_defaults(tmp_path):
    cfg = load(tmp_path, {})
    assert cfg.download.chunk_size == defaults()["download"]["chunk_size_mb"] * 1024 * 1024
    assert cfg.port == defaults()["port"]
    assert cfg.timing.op_timeout == defaults()["timing"]["op_timeout"]
    assert [str(n) for n in cfg.networks.nets] == defaults()["networks"]


def test_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_PATH", tmp_path / "missing.toml")
    assert config.load().path is None


def test_override_keeps_other_keys(tmp_path):
    cfg = load(tmp_path, {"timing": {"op_timeout": 3}, "download": {"destination": str(tmp_path)}})
    assert cfg.timing.op_timeout == 3
    assert cfg.timing.idle_timeout == defaults()["timing"]["idle_timeout"]
    assert cfg.download.destination == tmp_path
    assert cfg.download.subfolder == defaults()["download"]["subfolder"]


@pytest.mark.parametrize("raw", [{"hots": []}, {"timing": {"op_timeot": 3}}])
def test_unknown_setting_is_an_error(tmp_path, raw):
    with pytest.raises(config.ConfigError, match="unknown setting"):
        load(tmp_path, raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"networks": ["not a network"]},
        {"networks": "192.168.0.0/16"},
        {"hosts": "192.168.1.5"},
        {"hosts": [5]},
        {"timing": {"op_timeout": "soon"}},
        {"timing": {"op_timeout": True}},
        {"timing": 3},
        {"download": 3},
        {"guid": "nope"},
        {"port": "x"},
        {"port": 1.5},
        {"advertise": "false"},
        {"log_level": "loud"},
        {"port": True},
        {"port": 0},
        {"port": 65536},
        {"announce_port": 0},
        {"announce_port": 65536},
        {"ping_misses": 0},
        {"timing": {"op_timeout": -1}},
        {"timing": {"poll_interval": 0}},
        {"timing": {"event_poll": 0}},
        {"timing": {"retry_delay": -1}},
        {"timing": {"ping_timeout": 0.5}},
        {"download": {"chunk_size_mb": 0.0}},
        {"download": {"chunk_size_mb": 1 / 128}},
        {"download": {"chunk_size_mb": float("nan")}},
        {"timing": {"stall_timeout": float("inf")}},
        {"download": {"reserve_mb": 10**400}},
        {"download": {"chunk_size_mb": 17}},
        {"download": {"reserve_mb": -1}},
        {"download": {"subfolder": "{dat}"}},
        {"download": {"subfolder": "{date:%Y"}},
        {"download": {"subfolder": "{date[0]}"}},
        {"download": {"subfolder": "../{date:%Y}"}},
        {"download": {"subfolder": "/Volumes/X/{date:%Y}"}},
        {"download": {"subfolder": "{date:%Y}\0"}},
        {"download": {"subfolder": "{date:%Y}/../.."}},
        {"download": {"destination": "Pictures"}},
        {"name": "x" * 64},
        {"timing": {"connect_timeout": 180.0}},  # not less than stall_timeout
        {"timing": {"read_timeout": 180.0}},  # a read reports progress only when it ends
        {"timing": {"event_poll": 180.0}},
        *({"timing": {key: 0}} for key in ["poll_interval", "event_poll", "connect_window", "connect_timeout", "connect_retry", "op_timeout", "read_timeout", "stall_timeout"]),
    ],
)
def test_bad_value_is_an_error(tmp_path, raw):
    with pytest.raises(config.ConfigError, match=str(tmp_path)):
        load(tmp_path, raw)


def test_whole_numbers_are_accepted_for_seconds(tmp_path):
    assert load(tmp_path, {"timing": {"op_timeout": 3}}).timing.op_timeout == 3.0


def test_networks(tmp_path):
    cfg = load(tmp_path, {})
    for inside in ["192.168.0.204", "10.1.2.3", "172.16.0.1", "172.31.255.255", "169.254.9.9"]:
        assert inside in cfg.networks
    for outside in ["96.120.65.25", "172.32.0.1", "8.8.8.8", "127.0.0.1", "not an address"]:
        assert outside not in cfg.networks
    narrow = load(tmp_path, {"networks": ["192.168.0.0/23"]})
    assert "192.168.1.5" in narrow.networks and "10.0.0.1" not in narrow.networks


def test_interfaces_are_on_the_networks(tmp_path):
    cfg = load(tmp_path, {})
    assert all(address in cfg.networks for address in cfg.networks.interfaces().values())
    assert load(tmp_path, {"networks": ["203.0.113.0/24"]}).networks.interfaces() == {}


def test_guid_from_config_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_itu2_guid", lambda: uuid.UUID(int=1))
    guid = uuid.uuid4()
    assert load(tmp_path, {"guid": str(guid)}).guid == guid


def test_guid_from_itu2(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_itu2_guid", lambda: uuid.UUID(int=1))
    assert load(tmp_path, {}).guid == uuid.UUID(int=1)


def test_generated_guid_is_kept(tmp_path):
    first = load(tmp_path, {}).guid
    assert load(tmp_path, {}).guid == first
    assert (tmp_path / "state" / config.consts.STATE_GUID).read_text().strip() == str(first)


def test_log_file(tmp_path):
    assert load(tmp_path, {"log_file": ""}).log_file is None
    assert load(tmp_path, {"log_file": str(tmp_path / "x.log")}).log_file == tmp_path / "x.log"
    assert load(tmp_path, {}).log_file.name == config.consts.LOG_NAME


def test_malformed_file_is_an_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("hosts = [\n")
    with pytest.raises(config.ConfigError, match=str(path)):
        config.load(path)


def test_readme_example_loads(tmp_path):
    readme = (config.Path(__file__).parent.parent / "README.md").read_text()
    example = re.search(r"```toml\n(.*?)```", readme, re.S).group(1)
    path = tmp_path / "config.toml"
    path.write_text(tomli_w.dumps({"state_dir": str(tmp_path)}) + example)
    cfg = config.load(path)
    assert cfg.hosts == ["192.168.1.50"] and "192.168.1.50" in cfg.networks


@pytest.mark.parametrize(
    "raw",
    [
        {"port": 1},
        {"port": 65535},
        {"announce_port": 1},
        {"announce_port": 65535},
        {"ping_misses": 1},
        {"download": {"chunk_size_mb": 1 / 64}},
        {"download": {"chunk_size_mb": 16}},
        {"download": {"reserve_mb": 0}},
        {"download": {"subfolder": ""}},
        {"download": {"subfolder": "Camera/{date:%Y}"}},
        {"name": "x" * 63},
        {"timing": {"ping_timeout": 1}},
        {"timing": {"idle_timeout": 0}},
    ],
)
def test_values_at_the_limits_are_accepted(tmp_path, raw):
    load(tmp_path, raw)


def test_unreadable_file_is_a_config_error(tmp_path):
    (tmp_path / "config.toml").mkdir()
    with pytest.raises(config.ConfigError, match=str(tmp_path)):
        config.load(tmp_path / "config.toml")


def test_unusable_log_file_is_a_config_error(tmp_path):
    from cannonball.__main__ import arguments, supervisor

    path = tmp_path / "config.toml"
    load(tmp_path, {"log_file": str(tmp_path)})  # a folder
    with pytest.raises(config.ConfigError, match="log_file"):
        supervisor(arguments(["-c", str(path)]))


def test_homepage_matches_the_package_metadata():
    from cannonball import consts

    pyproject = tomllib.loads((config.Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["urls"]["Homepage"] == consts.HOMEPAGE  # About panel, update feed and PyPI agree
