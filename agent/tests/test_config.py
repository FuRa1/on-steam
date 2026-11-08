import pytest

from tvwake.config import ConfigError, load_config, normalize_mac, parse_env_file

from .conftest import make_env


def test_parse_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment\n\nA=1\nexport B = two \nC=\"with spaces\"\nD='single'\nURL=https://x/y?t={token}\nEMPTY=\n",
        encoding="utf-8",
    )
    env = parse_env_file(p)
    assert env == {"A": "1", "B": "two", "C": "with spaces", "D": "single", "URL": "https://x/y?t={token}", "EMPTY": ""}


def test_parse_env_file_bad_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("GOOD=1\nthis is not valid\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=":2:"):
        parse_env_file(p)


def test_defaults_moonlight():
    cfg = load_config(make_env())
    assert cfg.stream_backend == "moonlight"
    assert cfg.stream_port == 47989
    assert cfg.stream_app_id == "com.limelight.webos"
    assert cfg.pc_mac == "aa:bb:cc:dd:ee:ff"
    assert cfg.wol_ports == (9, 7)
    assert cfg.helper_bind == "127.0.0.1"
    assert cfg.helper_port == 8787


def test_steamlink_backend_and_overrides():
    cfg = load_config(make_env(STREAM_BACKEND="steamlink", STREAM_PORT="27037", WOL_PORTS="9"))
    assert cfg.stream_port == 27037
    assert cfg.stream_app_id == "com.valvesoftware.steamlink"
    assert cfg.wol_ports == (9,)


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"SHARED_SECRET": "short"}, "at least 32"),
        ({"PC_MAC": "zz:zz"}, "MAC"),
        ({"POLL_INTERVAL_S": "abc"}, "number"),
        ({"STREAM_BACKEND": "parsec"}, "STREAM_BACKEND"),
        ({"HELPER_PORT": "1.5"}, "whole number"),
        ({"MOCK_PC": "maybe"}, "0/1"),
        ({"PC_IP": ""}, "PC_IP"),
    ],
)
def test_invalid(overrides, match):
    with pytest.raises(ConfigError, match=match):
        load_config(make_env(**overrides))


def test_mock_mode_does_not_need_pc():
    env = make_env(MOCK_PC="1")
    del env["PC_IP"], env["PC_MAC"]
    cfg = load_config(env)
    assert cfg.mock_pc


@pytest.mark.parametrize("mac", ["aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"])
def test_normalize_mac(mac):
    assert normalize_mac(mac) == "aa:bb:cc:dd:ee:ff"
