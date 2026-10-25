#!/bin/sh
# Update the translation catalogues after changing user-facing strings.
#
#   tools/i18n.sh            extract strings, update each .po, compile .mo
#   tools/i18n.sh add LANG   start a new language (e.g. "es")
#
# Needs pybabel (apt install python3-babel). Translate the new or "fuzzy"
# entries in src/trafficcop/locale/<lang>/LC_MESSAGES/traffic-cop.po, then
# run this again to compile them. Both the .po and the .mo are committed, so
# building the package doesn't need pybabel.
set -eu
cd "$(dirname "$0")/.."
LOCALE=src/trafficcop/locale
POT=$LOCALE/traffic-cop.pot

pybabel extract --project=traffic-cop --no-wrap --sort-by-file \
    -k _ -k ngettext:1,2 -k pgettext:1c,2 \
    --ignore-dirs=legacy -o "$POT" src/trafficcop

if [ "${1:-}" = add ]; then
    pybabel init --no-wrap -i "$POT" -d "$LOCALE" -D traffic-cop -l "$2"
else
    pybabel update --no-wrap -i "$POT" -d "$LOCALE" -D traffic-cop
fi
pybabel compile --statistics -d "$LOCALE" -D traffic-cop
