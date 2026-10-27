"""Andika, SIL's font, for all the window's text.

Andika is open source (SIL Open Font License) and packaged as
``fonts-sil-andika``. A development machine uses the system's copy. The
snap stages the same package, but a strict snap can't count on seeing the
host's fonts, so ``register`` adds the snap's copy to fontconfig (which Tk
uses through Xft) before the window opens. Without Andika, Tk's default font
is used.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import tkinter as tk
from collections.abc import Mapping
from pathlib import Path
from tkinter import font

FAMILY = "Andika"
SIZE = 10
FONT_DIR = "usr/share/fonts/truetype/andika"
# Tk's named fonts; ttk widgets use these.
NAMED = (
    "TkDefaultFont",
    "TkTextFont",
    "TkHeadingFont",
    "TkMenuFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkTooltipFont",
)


def register(env: Mapping[str, str] = os.environ) -> list[Path]:
    """In the snap, make its bundled Andika files known to fontconfig.

    Call before creating the Tk root. Returns the files added (none outside
    the snap, or if fontconfig isn't available).
    """
    snap = env.get("SNAP")
    if not snap:
        return []
    files = sorted((Path(snap) / FONT_DIR).glob("*.ttf"))
    lib = ctypes.util.find_library("fontconfig")
    if not files or lib is None:
        return []
    fc = ctypes.CDLL(lib)
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    return [f for f in files if fc.FcConfigAppFontAddFile(None, os.fsencode(f))]


def apply(root: tk.Misc, size: int = SIZE) -> bool:
    """Make Andika the default for every widget, if Tk can find it."""
    if FAMILY not in font.families(root):
        return False
    for name in NAMED:
        try:
            font.nametofont(name, root).configure(family=FAMILY, size=size)
        except tk.TclError:
            pass  # not every Tk has every named font
    return True
