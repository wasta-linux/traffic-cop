import re

import pytest

from trafficcop.common import config
from trafficcop.common.config import ConfigError, format_rate, parse_rate


def get_app(cfg: config.Config, name: str) -> config.App:
    app = cfg.app(name)
    assert app is not None, f"no app {name!r}"
    return app


def fullmatch(pattern: re.Pattern[str] | None, text: str) -> bool:
    assert pattern is not None
    return pattern.fullmatch(text) is not None


# --- Rates --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "bits"),
    [
        ("800kbit", 800_000),
        ("100kbps", 800_000),  # kbps is kiloBYTES
        ("1.5mbit", 1_500_000),
        ("1mbps", 8_000_000),
        ("2gbit", 2_000_000_000),
        ("1kibps", 8192),
        ("1kibit", 1024),
        ("64bit", 64),
        ("10bps", 80),
        (" 70 kbps ", 560_000),
        (".5mbit", 500_000),
    ],
)
def test_parse_rate(text, bits):
    assert parse_rate(text) == bits


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (100, "needs a unit"),
        (1.5, "needs a unit"),
        ("100", "expected a number and a unit"),
        ("fast", "expected a number and a unit"),
        ("-5kbit", "expected a number and a unit"),
        ("0kbit", "greater than zero"),
        ("10kb", "unknown unit"),
        ("10Mbps", "units are lowercase, and 'mbps' means bytes"),
        ("10Kbit", "units are lowercase, and 'kbit' means bits"),
    ],
)
def test_parse_rate_rejects(value, message):
    with pytest.raises(ConfigError, match=message):
        parse_rate(value)


@pytest.mark.parametrize(
    ("bits", "text"),
    [
        (800_000, "800kbit"),
        (1_500_000, "1500kbit"),
        (8_000_000, "8mbit"),
        (2_000_000_000, "2gbit"),
        (8192, "8192bit"),
    ],
)
def test_format_rate(bits, text):
    assert format_rate(bits) == text
    assert parse_rate(text) == bits


# --- v2 parsing ---------------------------------------------------------------

GOOD = """
version: 2
apps:
  Zoom:
    download: 70kbps
    upload: 35kbps
    match:
      - snap: zoom-client
      - exe: /opt/zoom/zoom
        name: zoom
  Watch only:
    match:
      - scope: 'app-gnome-foo-\\d+\\.scope'
"""


def test_loads_good():
    cfg = config.loads(GOOD)
    zoom = get_app(cfg, "Zoom")
    assert zoom.download == 560_000
    assert zoom.upload == 280_000
    assert zoom.limited
    assert zoom.match[0].snap == "zoom-client"
    assert zoom.match[1].exe == "/opt/zoom/zoom"
    assert fullmatch(zoom.match[1].name, "zoom")

    watch = get_app(cfg, "Watch only")
    assert watch.download is None and watch.upload is None
    assert not watch.limited
    assert fullmatch(watch.match[0].scope, "app-gnome-foo-1234.scope")


def test_round_trip():
    cfg = config.loads(GOOD)
    assert config.parse(cfg.to_dict()) == cfg
    assert cfg.to_dict()["apps"]["Zoom"]["download"] == "560kbit"


def test_empty_apps_is_allowed():
    assert config.loads("version: 2\napps: {}\n").apps == ()
    assert config.loads("version: 2\n").apps == ()


def test_load_default():
    cfg = config.load_default()
    assert cfg.app("Zoom") is not None
    assert all(app.limited for app in cfg.apps)


def test_load_file(tmp_path):
    path = tmp_path / "traffic-cop.yaml"
    path.write_text(GOOD)
    assert config.load(path) == config.loads(GOOD)


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        config.load(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "config is empty"),
        ("# just a comment\n", "config is empty"),
        ("- a\n- b\n", "mapping at the top level"),
        ("version: 3\n", "unsupported version 3"),
        ("version: 2\nfoo: 1\n", "unknown key 'foo'"),
        ("version: 2\napps: [a]\n", "apps: expected a mapping"),
        ("version: 2\napps:\n  X:\n", r"apps\.X: expected a mapping"),
        ("version: 2\napps:\n  X:\n    download: 1mbit\n", r"apps\.X\.match"),
        ("version: 2\napps:\n  X:\n    match: []\n", r"apps\.X\.match"),
        (
            "version: 2\napps:\n  X:\n    dl: 1mbit\n    match: [{name: x}]\n",
            r"apps\.X: unknown key 'dl'",
        ),
        (
            "version: 2\napps:\n  X:\n    download: 5\n    match: [{name: x}]\n",
            r"apps\.X\.download: rate 5 needs a unit",
        ),
        (
            "version: 2\napps:\n  X:\n    match: [{pid: 1}]\n",
            r"apps\.X\.match\[0\]: unknown key 'pid'",
        ),
        (
            "version: 2\napps:\n  X:\n    match: [{}]\n",
            r"apps\.X\.match\[0\]: expected a mapping",
        ),
        (
            "version: 2\napps:\n  X:\n    match: [{name: '('}]\n",
            r"apps\.X\.match\[0\]\.name: invalid regex",
        ),
        (
            "version: 2\napps:\n  X:\n    match: [{exe: zoom}]\n",
            r"apps\.X\.match\[0\]\.exe: expected an absolute path",
        ),
        (
            "version: 2\napps:\n  X:\n    match: [{snap: 5}]\n",
            r"apps\.X\.match\[0\]\.snap: expected a non-empty string",
        ),
        (
            "version: 2\napps:\n  X: {match: [{name: x}]}\n  X: {match: [{name: y}]}\n",
            "duplicate key 'X'",
        ),
        ("version: 2\napps: {\n", "invalid YAML"),
    ],
)
def test_loads_rejects(text, message):
    with pytest.raises(ConfigError, match=message):
        config.loads(text)


def test_service_key():
    cfg = config.loads(
        "version: 2\napps:\n  S: {match: [{service: snapd}, {service: x.service}]}\n"
    )
    assert [m.service for m in get_app(cfg, "S").match] == [
        "snapd.service",
        "x.service",
    ]
    assert cfg.to_dict()["apps"]["S"]["match"][0] == {"service": "snapd.service"}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("{service: system.slice/snapd.service}", "expected a unit name"),
        ("{service: snapd, snap: x}", "can't be combined with snap, flatpak, scope"),
        ("{service: snapd, scope: x}", "can't be combined"),
    ],
)
def test_service_key_rejects(entry, message):
    with pytest.raises(ConfigError, match=message):
        config.loads(f"version: 2\napps:\n  S: {{match: [{entry}]}}\n")


def test_app_limit():
    apps = {f"a{i}": {"match": [{"name": "x"}]} for i in range(config.MAX_APPS)}
    assert len(config.parse({"version": 2, "apps": apps}).apps) == config.MAX_APPS
    apps["one too many"] = {"match": [{"name": "x"}]}
    with pytest.raises(ConfigError, match="at most 63 apps"):
        config.parse({"version": 2, "apps": apps})


# --- v1 configs ---------------------------------------------------------------


def test_v1_config_is_rejected():
    v1 = "download: 3mbit\nprocesses:\n  Zoom:\n    match: [{exe: /opt/zoom/zoom}]\n"
    with pytest.raises(
        ConfigError, match="Traffic Cop 1.x config, which isn't supported"
    ):
        config.loads(v1)


def test_missing_version():
    with pytest.raises(ConfigError, match="missing 'version: 2'"):
        config.loads("apps: {}\n")
