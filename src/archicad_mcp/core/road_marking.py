"""create_road_marking: place a Road Marking along a curve and drape it onto Meshes and
Slabs (docs/superpowers/specs/2026-10-12-road-marking-tool-design.md).

The Road Marking library part (Archicad GDL repo, Library/Site/Road Marking) keeps its
path as node arrays in its own coordinates, like the Swept Beam, with a cross fall in
percent per node. Its presets live in its Parameter Script; road_marking_presets.json
holds the same table, exported from the part's model by road_marking/export_presets.py
in the GDL repo, whose tests check that the two agree. This module works out the
marking's settings (the current or default values, the preset, the call's changes), how
far the marking reaches across its path, and what the part would draw past the path's
ends.
"""

from __future__ import annotations

import json
from pathlib import Path

from archicad_mcp.core import placed_path as pp

LIBRARY_PART = "Road Marking"
CUSTOM = "Custom"
TABLE = json.loads(Path(__file__).with_name("road_marking_presets.json").read_text(encoding="utf-8"))
PRESETS: dict[str, dict] = TABLE["presets"]
VALUES = TABLE["values"]
STRIPE_KEYS = {"on": "On", "offset": "Offset", "width": "Width", "dash": "Dash", "gap": "Gap",
               "angle": "Angle", "shape": "Shape"}
BAY_KEYS = {"layout": "bayLayout", "width": "bayWidth", "depth": "bayDepth", "zone_width": "zoneWidth",
            "zone_first": "zoneFirst", "bar_width": "barWidth", "symbol": "showSymbol"}
LETTER_KEYS = {"text": "letterText", "length": "letterLength", "width": "letterWidth", "gap": "letterGap",
               "offset": "letterOffset", "align": "letterAlign", "shift": "letterShift",
               "repeat": "letterRepeat"}
# A setting's kind is its name, or a stripe setting's suffix (s1Width is a Width)
BOOLEANS = {"On", "zoneFirst", "showSymbol", "flipSides", "wholeDashes"}
NUMBERS = {"Offset", "Width", "Dash", "Gap", "Angle", "bayWidth", "bayDepth", "zoneWidth", "barWidth",
           "letterLength", "letterWidth", "letterGap", "letterOffset", "letterShift", "letterRepeat",
           "patternShift", "thickness"}
# (minimum, maximum) as the part's Parameter Script ranges them; the part clamps silently, the tool refuses
RANGES = {"Width": (0.01, None), "Dash": (0.01, None), "Gap": (0.0, None), "Angle": (-80.0, 80.0),
          "bayWidth": (0.3, None), "bayDepth": (0.3, None), "zoneWidth": (0.1, None),
          "barWidth": (0.01, None), "letterLength": (0.1, None), "letterWidth": (0.05, None),
          "letterGap": (0.0, None), "letterRepeat": (0.0, None), "thickness": (0.0005, 0.05)}
ENUMS = {"Shape": VALUES["shapes"], "patternAlign": VALUES["aligns"], "bayLayout": VALUES["layouts"],
         "letterText": VALUES["letter_texts"], "letterAlign": VALUES["letter_aligns"]}


class RoadMarkingError(pp.PlacedPathError):
    """The request cannot become a Road Marking."""


def _kind(name: str) -> str:
    return name[2:] if len(name) > 2 and name[0] == "s" and name[1] in "123" else name


def preset_name(marking) -> str:
    """The part's own spelling of a preset name, matched without regard to case."""
    want = str(marking).strip().casefold()
    for name in [*PRESETS, CUSTOM]:
        if name.casefold() == want:
            return name
    raise RoadMarkingError(f"Unknown marking {marking!r}. Valid: {', '.join([*PRESETS, CUSTOM])}.")


def check_value(name: str, value):
    """The value the part takes for this setting, or RoadMarkingError."""
    kind = _kind(name)
    if kind in BOOLEANS:
        if not isinstance(value, bool):
            raise RoadMarkingError(f"{name} must be true or false, got {value!r}.")
        return value
    if kind in NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RoadMarkingError(f"{name} must be a number, got {value!r}.")
        value = float(value)
        lo, hi = RANGES.get(kind, (None, None))
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            span = f"at least {lo:g}" if hi is None else f"{lo:g} to {hi:g}"
            raise RoadMarkingError(f"{name} must be {span}, got {value:g}.")
        return value
    if kind in ENUMS and value not in ENUMS[kind]:
        raise RoadMarkingError(f"{name} must be one of: {', '.join(ENUMS[kind])}; got {value!r}.")
    return value


def resolve(current: dict, marking=None, stripes=None, bays=None, lettering=None, flat=None):
    """The marking's settings after the call (want), and the part's settings right after
    markingType is written (state): the current values, then the preset when the call picks
    one the marking does not already have (it replaces what it sets, as in the dialog),
    then the call's values."""
    state = dict(current)
    if marking is not None:
        name = preset_name(marking)
        if name != current["markingType"] and name in PRESETS:
            state.update(PRESETS[name])
        state["markingType"] = name
    want = dict(state)
    for key, change in (stripes or {}).items():
        if str(key) not in ("1", "2", "3"):
            raise RoadMarkingError(f"stripes keys are '1', '2' and '3', got {key!r}.")
        if not isinstance(change, dict):
            raise RoadMarkingError(f"stripes['{key}'] must be an object such as {{\"width\": 0.12}}.")
        for k, v in change.items():
            if k not in STRIPE_KEYS:
                raise RoadMarkingError(f"Unknown stripe key {k!r}. Valid: {', '.join(STRIPE_KEYS)}.")
            want[f"s{key}{STRIPE_KEYS[k]}"] = check_value(f"s{key}{STRIPE_KEYS[k]}", v)
    for group, keys, given in (("bays", BAY_KEYS, bays), ("lettering", LETTER_KEYS, lettering)):
        for k, v in (given or {}).items():
            if k not in keys:
                raise RoadMarkingError(f"Unknown {group} key {k!r}. Valid: {', '.join(keys)}.")
            want[keys[k]] = check_value(keys[k], v)
    for name, v in (flat or {}).items():
        if v is not None:
            want[name] = check_value(name, v)
    return want, state


def reach(p: dict) -> tuple[float, float]:
    """How far the marking reaches across its path, (u_lo, u_hi), positive to the right,
    after Flip sides: the switched on stripes, the bay layout and the word, and always 0
    (the part model's layout_reach and lettering_reach)."""
    flip = -1.0 if p["flipSides"] else 1.0
    lo = hi = 0.0
    for k in (1, 2, 3):
        if p[f"s{k}On"]:
            c, w = flip * float(p[f"s{k}Offset"]), float(p[f"s{k}Width"])
            lo, hi = min(lo, c - w / 2), max(hi, c + w / 2)
    if p["bayLayout"] != "None":
        d = flip * (float(p["bayDepth"]) + TABLE["line_width"] / 2)
        lo, hi = min(lo, d), max(hi, d)
    if p["letterText"] != "None":
        scale = float(p["letterWidth"]) / TABLE["letter_base_width"]
        width = (sum(TABLE["letter_widths"][ch] * scale for ch in p["letterText"])
                 + float(p["letterGap"]) * (len(p["letterText"]) - 1))
        centre = flip * float(p["letterOffset"])
        lo, hi = min(lo, centre - width / 2), max(hi, centre + width / 2)
    return lo, hi


def _start(align: str, length: float, size: float, shift: float) -> float:
    if align == "End":
        return length - shift - size
    if align == "Centre":
        return (length - size) / 2 + shift
    return shift


def overhang(p: dict, length: float) -> list[str]:
    """Warnings for a bay row or the placed word reaching past the path's ends, where the
    part draws it straight on but the drape does not reach."""
    out = []

    def note(what, size, start):
        before, after = -start, start + size - length
        if before > 0.001 or after > 0.001:
            out.append(f"{what} is {size:.2f} m long and reaches {max(before, 0.0):.2f} m before the start and "
                       f"{max(after, 0.0):.2f} m past the end of the {length:.2f} m path: the part draws it "
                       "there, but it is not draped.")

    layout = p["bayLayout"]
    if layout not in ("None", "Stop end bars"):
        bay, zone = float(p["bayWidth"]), float(p["zoneWidth"])
        cells = {"Disabled bays, shared zone": [bay, zone, bay], "Taxi bay": [bay],
                 "EV charging bay": [bay]}.get(layout, [zone, bay])
        row = sum(cells)
        note("The bay row", row, _start(p["patternAlign"], length, row, float(p["patternShift"])))
    if p["letterText"] != "None":
        size = float(p["letterLength"])
        note(f"The word {p['letterText']}", size, _start(p["letterAlign"], length, size, float(p["letterShift"])))
    return out


def no_effect(p: dict, bays, lettering) -> list[str]:
    out = []
    if p["bayLayout"] == "None" and any(k != "layout" for k in (bays or {})):
        out.append("The bays values have no effect while the bay layout is None.")
    if p["letterText"] == "None" and any(k != "text" for k in (lettering or {})):
        out.append("The lettering values have no effect while the word is None.")
    return out
