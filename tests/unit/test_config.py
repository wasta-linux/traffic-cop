import logging
from pathlib import Path

import pytest

from trafficcop.common import config
from trafficcop.common.config import ConfigError, format_rate, parse_rate

DATA = Path(__file__).parent.parent / "data"


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
    zoom = cfg.app("Zoom")
    assert zoom.download == 560_000
    assert zoom.upload == 280_000
    assert zoom.limited
    assert zoom.match[0].snap == "zoom-client"
    assert zoom.match[1].exe == "/opt/zoom/zoom"
    assert zoom.match[1].name.fullmatch("zoom")

    watch = cfg.app("Watch only")
    assert watch.download is None and watch.upload is None
    assert not watch.limited
    assert watch.match[0].scope.fullmatch("app-gnome-foo-1234.scope")


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


# --- v1 migration -------------------------------------------------------------


def test_migrate_v1_sample(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = config.load(DATA / "traffic-cop.yaml")
    assert "ignoring global settings" in caplog.text
    assert "ignoring priorities" in caplog.text

    assert [a.name for a in cfg.apps] == [
        "Speedtest",
        "Skype",
        "Insync",
        "Spotify",
        "Zoom",
    ]
    assert not cfg.app("Speedtest").limited  # priority only in v1
    insync = cfg.app("Insync")
    assert insync.download == 800_000
    assert insync.match[0].exe == "/usr/lib/insync/insync"


def test_migrate_v1_default():
    cfg = config.load(DATA / "traffic-cop.yaml.default")
    assert [a.name for a in cfg.apps] == ["Speedtest", "Skype", "Zoom"]
    assert cfg.app("Zoom").match[0].exe == "/opt/zoom/zoom"


def test_migrate_v1_merges_predicates():
    data = {"processes": {"X": {"match": [{"cmdline": ".*x.*"}, {"name": "x"}]}}}
    assert config.migrate_v1(data)["apps"]["X"]["match"] == [
        {"cmdline": ".*x.*", "name": "x"}
    ]
    app = config.parse(data).app("X")
    assert app.match[0].cmdline.fullmatch("/usr/bin/x --new-window")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("bad_syntax.yaml", r"processes\.Fake: expected a mapping"),
        ("bad_syntax_indent.yaml", r"processes\.Fake: expected a mapping"),
        ("bad_syntax_no_match.yaml", r"processes\.Firefox\.match"),
        ("bad_syntax_wrong_parameter.yaml", "unknown key 'dl'"),
        ("empty.yaml", "config is empty"),
    ],
)
def test_bad_samples(name, message):
    with pytest.raises(ConfigError, match=message):
        config.load(DATA / name)
