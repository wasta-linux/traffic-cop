import grp

import pytest

from trafficcop.common import config, i18n
from trafficcop.gui.editor import (
    AppDraft,
    EditError,
    Limit,
    may_change,
    with_app,
    without_app,
)

CFG = config.load_default().to_dict()  # Firefox, Snap Updates, Spotify, Zoom


@pytest.mark.parametrize(
    ("bits", "limit"),
    [
        (None, Limit("", "kB/s")),
        (560_000, Limit("70", "kB/s")),
        (500_000, Limit("62.5", "kB/s")),
        (16_000_000, Limit("2", "MB/s")),
        (64_000, Limit("8", "kB/s")),
    ],
)
def test_limit_from_bits(bits: int | None, limit: Limit):
    assert Limit.from_bits(bits) == limit


@pytest.mark.parametrize(
    ("number", "unit", "rate"),
    [
        ("70", "kB/s", "70kbps"),
        ("62,5", "kB/s", "62.5kbps"),  # French decimal comma
        (" 1.5 ", "MB/s", "1.5mbps"),
        ("", "kB/s", None),
        ("  ", "MB/s", None),
    ],
)
def test_limit_to_rate(number: str, unit: str, rate: str | None):
    assert Limit(number, unit).to_rate("x") == rate


@pytest.mark.parametrize("number", ["0", "-5", "abc", "1.2.3", "1e3"])
def test_limit_rejects(number: str):
    with pytest.raises(EditError, match="Download: enter a number greater than 0"):
        Limit(number).to_rate("Download")


def test_round_trip_through_the_config():
    draft = AppDraft.from_entry("Zoom", CFG["apps"]["Zoom"])
    assert draft.download == Limit("70", "kB/s")
    assert draft.rules == [("snap", "zoom-client"), ("exe", "/opt/zoom/zoom")]
    name, entry = draft.to_entry()
    assert config.parse({"version": 2, "apps": {name: entry}}) == config.parse(
        {"version": 2, "apps": {"Zoom": CFG["apps"]["Zoom"]}}
    )


def test_multi_key_rules_are_kept_unchanged():
    entry = {"match": [{"snap": "x"}, {"name": "zoom", "exe": "/opt/zoom/zoom"}]}
    draft = AppDraft.from_entry("Z", entry)
    assert draft.rules == [("snap", "x")]
    assert draft.fixed_rules == [{"name": "zoom", "exe": "/opt/zoom/zoom"}]
    assert draft.to_entry()[1]["match"] == entry["match"]


def test_to_entry():
    draft = AppDraft(
        " Zoom ", Limit("70"), Limit(""), [("exe", " /opt/zoom/zoom "), ("snap", "")]
    )
    assert draft.to_entry() == (
        "Zoom",
        {"download": "70kbps", "match": [{"exe": "/opt/zoom/zoom"}]},
    )


@pytest.mark.parametrize(
    ("draft", "message"),
    [
        (AppDraft("", rules=[("snap", "x")]), "Give the app a name"),
        (AppDraft("X"), "at least one rule"),
        (AppDraft("X", rules=[("snap", "  ")]), "at least one rule"),
        (AppDraft("X", rules=[("name", "(")]), "invalid regex"),
        (AppDraft("X", rules=[("exe", "zoom")]), "absolute path"),
        (AppDraft("X", Limit("fast"), rules=[("snap", "x")]), "Download limit"),
    ],
)
def test_to_entry_rejects(draft: AppDraft, message: str):
    with pytest.raises(EditError, match=message):
        draft.to_entry()


def test_errors_are_translated():
    i18n.setup(["fr"])
    with pytest.raises(EditError, match="Donnez un nom"):
        AppDraft().to_entry()


def test_with_app_adds_at_the_end():
    new = with_app(CFG, None, "Skype", {"match": [{"snap": "skype"}]})
    assert list(new["apps"]) == ["Firefox", "Snap Updates", "Spotify", "Zoom", "Skype"]
    assert config.parse(new)


def test_with_app_renames_in_place():
    entry = {"download": "1mbit", "match": [{"snap": "spotify"}]}
    new = with_app(CFG, "Spotify", "Music", entry)
    assert list(new["apps"]) == ["Firefox", "Snap Updates", "Music", "Zoom"]
    assert new["apps"]["Music"] == entry


def test_with_app_refuses_duplicates():
    with pytest.raises(EditError, match="already an app called Zoom"):
        with_app(CFG, "Spotify", "Zoom", {"match": [{"snap": "x"}]})
    with pytest.raises(EditError):
        with_app(CFG, None, "Zoom", {"match": [{"snap": "x"}]})


def test_without_app():
    assert list(without_app(CFG, "Spotify")["apps"]) == [
        "Firefox",
        "Snap Updates",
        "Zoom",
    ]
    assert without_app(CFG, "Nope")["apps"].keys() == CFG["apps"].keys()


def test_may_change():
    assert may_change(groups=[], uid=0)
    assert not may_change(groups=[], uid=1000)
    try:
        sudo = grp.getgrnam("sudo").gr_gid
    except KeyError:
        pytest.skip("no sudo group here")
    assert may_change(groups=[sudo], uid=1000)


def test_limits_show_the_interface_decimal_separator():
    i18n.setup(["fr"])
    assert Limit.from_bits(500_000) == Limit("62,5", "kB/s")
    assert Limit("62,5").to_rate("x") == "62.5kbps"
