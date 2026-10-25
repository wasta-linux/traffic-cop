"""Translations (gettext) for traffic-cop-ctl and the window.

Catalogues live in the package, ``trafficcop/locale/<lang>/LC_MESSAGES/
traffic-cop.mo``, so they ship with it (the snap included). The language
comes from the usual variables (``LANGUAGE``, ``LC_ALL``, ``LC_MESSAGES``,
``LANG``); English is the fallback. Supported for now: English, French.

Numbers follow the interface language too, not the system locale: the
decimal separator is itself a translatable string (context "decimal
separator"), so French shows "45,8 ko/s" even where no French locale data is
installed, as in a snap. Units are translated like any other string
("kB/s" -> "ko/s").

The daemon's log stays in English. Messages that come from the daemon
(e.g. config validation errors) aren't translated.

To update the catalogues after changing strings: ``tools/i18n.sh``.
"""

from __future__ import annotations

import gettext
from importlib import resources
from pathlib import Path

DOMAIN = "traffic-cop"
LOCALE_DIR = Path(str(resources.files("trafficcop") / "locale"))

_translation: gettext.NullTranslations = gettext.NullTranslations()


def setup(languages: list[str] | None = None) -> None:
    """Load the catalogue for ``languages`` (default: from the environment)."""
    global _translation
    _translation = gettext.translation(
        DOMAIN, LOCALE_DIR, languages=languages, fallback=True
    )


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    return _translation.pgettext(context, message)


def number(value: float, decimals: int = 1) -> str:
    """``value`` with ``decimals`` places and the language's decimal separator."""
    text = f"{value:.{decimals}f}"
    return text.replace(".", pgettext("decimal separator", "."))


setup()
