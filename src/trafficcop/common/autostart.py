"""Start Traffic Cop in the tray at login: on by default, switched off in the window.

snapd does the starting (see ``paths.autostart_file``); this module only
writes the desktop file it looks for. The file has three states:

- missing: the user hasn't chosen, so the default applies: ``set_default``
  turns it on (the window calls it once the tray icon is shown, so desktops
  without a tray never get it: there, a hidden start would only quit);
- ``Hidden=false``: on;
- ``Hidden=true``: off. snapd skips hidden files (``ShouldAutostart`` in
  snapd's desktop/desktopentry), and keeping the file, rather than deleting
  it, remembers the choice so the default doesn't turn it back on.

snapd replaces the first word of ``Exec=`` with the app's own launcher and
keeps the rest (usersession/autostart in snapd), so only ``--hidden``
matters there.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=Traffic Cop
Exec=wasta-traffic-cop.gui --hidden
Hidden={hidden}
"""


def enabled(path: Path) -> bool:
    """On: the file exists and isn't hidden."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return False
    return "Hidden=true" not in text.splitlines()


def set_enabled(path: Path, on: bool) -> None:
    """Turn starting at login on or off. Raises OSError if it can't be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(hidden="false" if on else "true"))


def set_default(path: Path) -> None:
    """Turn it on, unless the user has already chosen."""
    if not path.exists():
        set_enabled(path, True)
