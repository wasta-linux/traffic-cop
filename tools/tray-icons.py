#!/usr/bin/env python3
"""Make the tray icons from the app icon, after changing snap/gui/icon.svg.

    pip install shapely        # only this script needs it
    python3 tools/tray-icons.py

Writes wasta-traffic-cop-{on,paused,offline}-symbolic.svg in
src/trafficcop/indicator/icons/: monochrome versions of the app icon (the
yield sign and the officer).

    on       full opacity (limits on)
    paused   faint (limits off)
    offline  faint, with a bold X over it (the daemon can't be reached)

Why they're drawn this way:

- Panels recolour monochrome tray icons to match their own text (white on
  GNOME's dark bar, dark on a light KDE panel): GNOME through the
  "-symbolic" name, which makes it override every shape's *fill*; KDE
  through the ColorScheme-Text class and currentColor. Only opacity
  survives, so on/paused differ by opacity, and everything is a filled
  shape (a stroked X would keep its own colour in GNOME).
- In one colour, the sign's white parts must become holes (the inside of
  the triangle), and where the officer overlaps the red band a gap must
  separate them, or they'd merge. That's shape arithmetic (shapely), done
  here once rather than with SVG masks, which Qt (KDE) doesn't support.
  The output is plain filled paths.

The app icon has hidden layers (an older octagon design); only the visible
ones are used. The shapes are taken by id; if the icon is redrawn, update
the ids and transforms below (each path's transform, then its groups'
outwards, as in the file).
"""

from __future__ import annotations

import re
from pathlib import Path

from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "snap/gui/icon.svg"
OUT = ROOT / "src/trafficcop/indicator/icons"

# SVG matrix(a, b, c, d, e, f) values.
OFFICER_GROUP = (1.1214344, 0, 0, 1.1214344, -7.3571162, -1.7282734)  # g53
SHAPES = {
    # id: (transforms innermost first, stroke width or 0)
    "red": (
        "path96",
        [(1.0192905, 0.17579995, -0.1758015, 1.0192815, 68.5149, -2.9134836)],
        3.62406588,
    ),
    "inside": (
        "path96-5",
        [(0.56395299, 0.09726744, -0.09726744, 0.56395299, 53.170214, 11.620818)],
        2.99635005,
    ),
    "officer": (
        "path4584-7",
        [(1.2484889, 0, 0, 1.2484889, -9.6541583, -285.54032), OFFICER_GROUP],
        0,
    ),
    "details": (
        "path4584-7-6",
        [(1.2484889, 0, 0, 1.2484889, -9.6540834, -285.54032), OFFICER_GROUP],
        0,
    ),
}

GAP = 1.5  # between the officer and the band, in the icon's units (67.7 wide)
MIN_AREA = 4.0  # smaller pieces of the band are dropped
FAINT = 0.35
X_HALF_WIDTH = 0.065  # of each bar of the X, as a fraction of the icon size
X_MARGIN = 0.1
CURVE_STEPS = 16  # straight segments per Bézier curve


def path_data(svg: str, path_id: str) -> str:
    for element in re.findall(r"<path\b[^>]*>", svg, re.S):
        if re.search(rf'\bid="{re.escape(path_id)}"', element):
            match = re.search(r'\bd="([^"]+)"', element)
            if match:
                return match.group(1)
    raise SystemExit(f"{SRC} has no path {path_id!r}; update this script")


def rings(d: str) -> list[list[tuple[float, float]]]:
    """The closed outlines of SVG path data, with curves as short lines.

    Handles the commands the app icon uses: M/m, L/l, H/h, V/v, C/c, Z/z.
    """
    tokens = re.findall(r"[MmLlHhVvCcZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    out: list[list[tuple[float, float]]] = []
    ring: list[tuple[float, float]] = []
    x = y = 0.0
    start = (0.0, 0.0)
    cmd = ""
    i = 0

    def num() -> float:
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if ring:
                    out.append(ring)
                ring = []
                x, y = start
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            if ring:
                out.append(ring)
            dx, dy = num(), num()
            x, y = (x + dx, y + dy) if rel else (dx, dy)
            start = (x, y)
            ring = [(x, y)]
            cmd = "l" if rel else "L"  # later pairs are line-tos
        elif c == "L":
            dx, dy = num(), num()
            x, y = (x + dx, y + dy) if rel else (dx, dy)
            ring.append((x, y))
        elif c == "H":
            v = num()
            x = x + v if rel else v
            ring.append((x, y))
        elif c == "V":
            v = num()
            y = y + v if rel else v
            ring.append((x, y))
        elif c == "C":
            pts = [num() for _ in range(6)]
            if rel:
                pts = [p + (x if k % 2 == 0 else y) for k, p in enumerate(pts)]
            x1, y1, x2, y2, x3, y3 = pts
            for step in range(1, CURVE_STEPS + 1):
                t = step / CURVE_STEPS
                u = 1 - t
                ring.append(
                    (
                        u**3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                        u**3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
                    )
                )
            x, y = x3, y3
        else:
            raise SystemExit(f"path command {cmd!r} not handled; update this script")
    if ring:
        out.append(ring)
    return out


def shape(svg: str, path_id: str, transforms: list, stroke: float) -> BaseGeometry:
    """A path's filled area (plus its round-joined stroke) in icon units."""
    # Overlapping subpaths (e.g. the officer's arm over his body) are
    # unioned: the icon uses the default nonzero fill, not evenodd.
    geom = unary_union([Polygon(r).buffer(0) for r in rings(path_data(svg, path_id))])
    if stroke:
        geom = geom.buffer(stroke / 2, join_style="round")
    for a, b, c, d, e, f in transforms:
        geom = affinity.affine_transform(geom, [a, c, b, d, e, f])
    return geom


def to_path(geom: BaseGeometry) -> str:
    polygons = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    parts = []
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            points = list(ring.coords)[:-1]
            parts.append("M " + " ".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z")
    return " ".join(parts)


def x_bars(x0: float, y0: float, size: float) -> str:
    """An X as two filled bars (corner to corner), as path data."""
    lo, hi, w = x0 + size * X_MARGIN, x0 + size * (1 - X_MARGIN), size * X_HALF_WIDTH
    top, bottom = y0 + size * X_MARGIN, y0 + size * (1 - X_MARGIN)
    bars = [
        [(lo, top + w), (lo + w, top), (hi, bottom - w), (hi - w, bottom)],
        [(hi - w, top), (hi, top + w), (lo + w, bottom), (lo, bottom - w)],
    ]
    return " ".join(
        "M " + " ".join(f"{x:.2f},{y:.2f}" for x, y in bar) + " Z" for bar in bars
    )


def icon(view: tuple[float, float, float], d: str, opacity: float, x: str = "") -> str:
    x0, y0, size = view

    def path(data: str, alpha: float) -> str:
        return (
            f'  <path class="ColorScheme-Text" fill="currentColor" '
            f'fill-rule="evenodd" opacity="{alpha:g}" d="{data}"/>\n'
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
        f'viewBox="{x0:.2f} {y0:.2f} {size:.2f} {size:.2f}">\n'
        # The default colour (light, for dark panels); KDE replaces it.
        '  <style id="current-color-scheme">'
        ".ColorScheme-Text{color:#dfdfdf;}</style>\n"
        + path(d, opacity)
        + (path(x, 1) if x else "")
        + "</svg>\n"
    )


def main() -> None:
    svg = SRC.read_text(encoding="utf-8")
    s = {name: shape(svg, *spec) for name, spec in SHAPES.items()}
    # The officer as a solid silhouette (cutting out his face and shirt left
    # thin stripes at tray size), the sign as its red band, with a gap
    # between them. Crumbs the gap cuts off (the triangle's tip) are dropped.
    officer = s["officer"].union(s["details"])
    band = s["red"].difference(s["inside"]).difference(officer.buffer(GAP))
    whole = unary_union(
        [p for p in getattr(band, "geoms", [band]) if p.area > MIN_AREA] + [officer]
    )

    # Fill the icon: a square around the drawing, as panels draw it square.
    minx, miny, maxx, maxy = whole.bounds
    size = max(maxx - minx, maxy - miny)
    view = (minx - (size - (maxx - minx)) / 2, miny - (size - (maxy - miny)) / 2, size)

    d = to_path(whole)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    icons = {
        "on": icon(view, d, 1),
        "paused": icon(view, d, FAINT),
        "offline": icon(view, d, FAINT, x_bars(*view)),
    }
    for state, text in icons.items():
        target = OUT / f"wasta-traffic-cop-{state}-symbolic.svg"
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)} ({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
