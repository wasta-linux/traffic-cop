import gettext
import string

import pytest

from tests.unit.test_cli import USAGE, FakeDaemon, run
from tests.unit.test_gui_model import STATUS
from trafficcop.cli import __main__ as cli
from trafficcop.common import display, i18n
from trafficcop.gui.model import build_view

LANGUAGES = ["fr"]


@pytest.fixture
def french() -> None:
    i18n.setup(["fr"])


def test_english_is_the_default():
    i18n.setup(["xx"])  # no such catalogue
    assert i18n._("Limits on") == "Limits on"
    assert i18n.number(45.8) == "45.8"


def test_follows_the_environment(monkeypatch: pytest.MonkeyPatch):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "fr_CA.UTF-8")
    i18n.setup()
    assert i18n._("Limits on") == "Limites activées"


def test_french_numbers_and_units(french):
    assert i18n.number(45.8) == "45,8"
    assert display.rate_of_limit(45_800, 560_000) == "45,8 / 70 ko/s"
    assert display.rate_of_limit(900, 500_000) == "0,9 / 62,5 ko/s"
    assert display.human_bytes(31_200_000) == "31,2 Mo"
    assert display.human_bytes(512) == "512 o"
    assert display.human_rate(2_500_000) == "2,5 Mo/s"


def test_french_status(french):
    assert display.state_text(STATUS) == "limitation du trafic sur wlp0s20f3"
    view = build_view(None, None, FileNotFoundError(2, "x"))
    assert view.headline == "Traffic Cop n’est pas lancé"
    assert view.detail.startswith("Lancez-le avec : sudo ")


def test_french_plurals(french):
    assert i18n.ngettext("{n} unit", "{n} units", 1) == "{n} unité"
    assert (
        i18n.ngettext("{n} unit", "{n} units", 0) == "{n} unité"
    )  # French: 0 is singular
    assert i18n.ngettext("{n} unit", "{n} units", 2) == "{n} unités"


def test_french_cli(french):
    code, out = run(FakeDaemon(), "rates")
    assert code == 0
    assert out.splitlines()[0].split() == [
        "APPLI",
        "RÉCEPTION",
        "ENVOI",
        "TOTAL",
        "REÇU",
        "TOTAL",
        "ENVOYÉ",
    ]
    assert "65,4 ko/s" in out and "31,0 Mo" in out
    assert USAGE["apps"]["Firefox"]["down_rate"] == 65400.0


def test_french_cli_errors(french, capsys: pytest.CaptureFixture[str]):
    daemon = FakeDaemon()
    daemon.error = ConnectionRefusedError()
    assert run(daemon, "status")[0] == cli.EXIT_UNREACHABLE
    assert "impossible de joindre le démon" in capsys.readouterr().err


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_catalogue_is_complete_and_keeps_placeholders(lang: str):
    t = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[lang])
    catalog: dict[object, str] = t._catalog  # type: ignore[attr-defined]
    assert len(catalog) > 40
    for key, translated in catalog.items():
        if key == "":
            continue  # the header
        msgid = str(key[0] if isinstance(key, tuple) else key)
        msgid = msgid.split("\x04")[-1]  # drop a context
        assert translated, f"{lang}: {msgid!r} is untranslated"
        assert _fields(translated) == _fields(msgid), f"{lang}: {msgid!r}"
