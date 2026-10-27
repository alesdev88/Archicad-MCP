"""create_swept_beam: place a Swept Beam along a curve (spec Part 3).

The Swept Beam library part (gdl-src/swept-beam) stores its path as node
arrays in its own coordinates. This module reads a source curve from the
model or takes 3D points, fits it into nodes with swept_path, and writes the
nodes into a new or an existing Swept Beam.

Tapir reports z as an absolute elevation (story elevation plus offset) for
morphs, polylines, lines, arcs and placed objects, and CreateObjects derives
the story from an absolute z, so this module works in absolute z throughout.
"""

from __future__ import annotations

import math

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import placed_path as pp
from archicad_mcp.core import swept_path as sp

LIBRARY_PART = "Swept Beam"
NEAR_VERTICAL_DEG = 85.0
# End cut limits, matching the part: a plan cut within 80 degrees of square
# and a tilt within 85 degrees keep the cut face crossing the beam.
# Where the beam sits on its reference line (the nodes), wall style.
REF_LINES = {"left face": "Left face", "centre": "Centre", "center": "Centre",
             "right face": "Right face"}
CUT_LIMITS = {"cut_start_plan": ("cutStartPlan", 80.0), "cut_start_tilt": ("cutStartTilt", 85.0),
              "cut_end_plan": ("cutEndPlan", 80.0), "cut_end_tilt": ("cutEndTilt", 85.0)}
NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Build it with "
              "'archicad-gdl build-source gdl-src/swept-beam --out <GDL workspace>' "
              "into the linked GDL workspace folder, then reload libraries.")


class SweptBeamError(pp.PlacedPathError):
    """The request cannot become a Swept Beam."""


def create_swept_beam(conn: ArchicadConnection, source_guid: str | None = None,
                      points: list[dict] | str | None = None,
                      start_height: float | None = None,
                      slope_percent: float | None = None, profile: dict | str | None = None,
                      offset_u: float | None = None, offset_w: float | None = None,
                      flip: bool | None = None, path_tolerance: float = 0.002,
                      update_guid: str | None = None, dry_run: bool = True,
                      cut_start_plan: float | None = None, cut_start_tilt: float | None = None,
                      cut_end_plan: float | None = None,
                      cut_end_tilt: float | None = None, ref_line: str | None = None,
                      ref_offset: float | None = None) -> dict:
    """Section and end cut values left as None take the defaults on a new beam
    (a 0.2 x 0.2 rectangle, no offsets, no flip, square ends) and keep the
    beam's own values on an update.

    End cuts are degrees: the plan angle turns the cut counter-clockwise from
    square in plan; the tilt leans the top of the face out past the end, 0
    being square to the beam.

    ref_line puts the beam's left face, centre or right face (looking from the
    first node to the last) on the reference line, the nodes; ref_offset moves
    the beam that far away from it (with centre, to the right)."""
    cuts = {"cut_start_plan": cut_start_plan, "cut_start_tilt": cut_start_tilt,
            "cut_end_plan": cut_end_plan, "cut_end_tilt": cut_end_tilt,
            "ref_line": ref_line, "ref_offset": ref_offset}
    try:
        points = pp.from_json_text(points, "points", list)
        profile = pp.from_json_text(profile, "profile", dict)
        return _create(conn, source_guid, points, start_height, slope_percent, profile,
                       offset_u, offset_w, flip, path_tolerance, update_guid, dry_run, cuts)
    except (sp.PathError, pp.PlacedPathError) as exc:
        return {"error": str(exc)}


def _create(conn, source_guid, points, start_height, slope_percent, profile,
            offset_u, offset_w, flip, path_tolerance, update_guid, dry_run, cuts) -> dict:
    if (source_guid is None) == (points is None):
        raise SweptBeamError("Give exactly one of source_guid or points.")
    if path_tolerance <= 0:
        raise SweptBeamError("path_tolerance must be a positive length in metres.")
    radians = pp.angles_in_radians(conn)
    cut_params = (_cut_params(cuts, keep_unset=update_guid is not None, radians=radians)
                  + _ref_params(cuts, keep_unset=update_guid is not None))
    warnings: list[str] = []
    if source_guid is not None:
        path, floor_index, flat = pp.path_from_element(conn, source_guid, path_tolerance, LIBRARY_PART)
    else:
        path, floor_index, flat = pp.path_from_points(points, path_tolerance), None, False
    if flat:
        base = path.nodes[0][2] + (start_height or 0.0)
        path.nodes = sp.apply_slope(path.nodes, path.arcs, base, slope_percent or 0.0)
    elif start_height is not None or slope_percent is not None:
        warnings.append("start_height and slope_percent apply to flat sources only; "
                        "this source keeps its own heights.")
    steepest = path.steepest_slope_deg()
    if steepest > NEAR_VERTICAL_DEG:
        warnings.append(f"A piece rises at {steepest:.1f} degrees. Near vertical, the "
                        "plumb section orientation is undefined.")
    profile_params = _profile_params(conn, profile, offset_u, offset_w, flip,
                                     keep_unset=update_guid is not None)
    loaded = pp.library_part_loaded(conn, LIBRARY_PART)
    if not loaded:
        warnings.append(NOT_LOADED)
    current = None
    profiles = None
    if update_guid is not None:
        origin, angle = pp.existing_frame(conn, update_guid, LIBRARY_PART)
        # the beam's own segment profiles, kept while the node count holds
        current = pp.gdl_parameters(conn, update_guid)
        profiles, lost = _kept_profiles(current, len(path.nodes))
        if lost:
            warnings.append(f"The new path has {len(path.nodes)} nodes instead of "
                            f"{len(current.get('nodeX') or [])}, so every segment goes back to "
                            f"Profile 1 ({lost} were on Profile 2 or 3).")
    else:
        origin, angle = path.nodes[0], 0.0
    local = pp.to_local(path.nodes, origin, angle)
    items = pp.writes_items(conn)
    if items:
        pp.check_plan_gaps(local, LIBRARY_PART)
    params = (_node_params(local, path.arcs, path_tolerance, items, radians, profiles)
              + profile_params + cut_params)
    report = {
        "dry_run": dry_run,
        "nodes": len(path.nodes),
        "arcs": path.arc_count,
        "straight": path.straight_count,
        "max_deviation_mm": round(path.max_deviation * 1000, 2),
        "steepest_slope_deg": round(steepest, 1),
        "origin": {"x": origin[0], "y": origin[1], "z": origin[2]},
        "story": floor_index,
        "array_writes": "item by item" if items else "whole arrays",
        "warnings": warnings,
    }
    if dry_run:
        report["gdl_parameters"] = params
        return report
    if not loaded:
        raise SweptBeamError(NOT_LOADED)
    guid = update_guid or pp.place(conn, LIBRARY_PART, origin, floor_index)
    if items:
        # no resizing: reset the path, grow it to the node count, then write every item
        if current is None:
            current = pp.gdl_parameters(conn, guid)
        params = (pp.resize_writes(current, len(local)) + params
                  + _kept_cuts(current, {p["name"] for p in params}, radians))
    pp.write_parameters(conn, guid, params, LIBRARY_PART)
    report["element"] = guid
    report["updated" if update_guid else "created"] = True
    return report


def _profile_params(conn, profile, offset_u, offset_w, flip, keep_unset=False) -> list[dict]:
    """Section parameters. With keep_unset (rewriting a placed beam), a value the
    call leaves as None is not written, so the beam keeps its own."""
    common = []
    for name, value, default, conv in (("profileOffsetU", offset_u, 0.0, float),
                                       ("profileOffsetW", offset_w, 0.0, float),
                                       ("flipProfile", flip, False, bool)):
        if value is None:
            if keep_unset:
                continue
            value = default
        common.append({"name": name, "value": conv(value)})
    if not profile:
        if keep_unset:
            return common
        profile = {"rectangle": {"width": 0.2, "height": 0.2}}
    if "attribute" in profile:
        index = pp.attribute_index(conn, "Profile", str(profile["attribute"]))
        return [{"name": "profileMode", "value": "Profile attribute"},
                {"name": "beamProfile", "value": index}] + common
    if "rectangle" in profile:
        rect = profile["rectangle"] or {}
        w, h = float(rect.get("width", 0.2)), float(rect.get("height", 0.2))
        if w <= 0 or h <= 0:
            raise SweptBeamError("Rectangle width and height must be positive.")
        out = [{"name": "profileMode", "value": "Rectangle"},
               {"name": "rectW", "value": w}, {"name": "rectH", "value": h}]
        if rect.get("building_material"):
            out.append({"name": "rectBMat", "value": pp.attribute_index(
                conn, "BuildingMaterial", str(rect["building_material"]))})
        return out + common
    raise SweptBeamError('profile must be {"attribute": "<name>"} or '
                         '{"rectangle": {"width": w, "height": h, "building_material": "<name>"}}.')


def _cut_params(cuts: dict, keep_unset: bool, radians: bool = False) -> list[dict]:
    """End cut angles, given in degrees: Tapir up to 1.7.2 takes a single angle
    parameter in degrees (gate A), 1.7.3 and newer in radians. On an update, a
    cut the call leaves as None is kept."""
    out = []
    for arg, (name, limit) in CUT_LIMITS.items():
        value = cuts.get(arg)
        if value is None:
            if keep_unset:
                continue
            value = 0.0
        value = float(value)
        if abs(value) > limit:
            raise SweptBeamError(f"{arg} must be between -{limit:g} and {limit:g} degrees, "
                                 f"got {value:g}.")
        out.append({"name": name, "value": math.radians(value) if radians else value})
    return out


def _kept_cuts(current: dict, written: set[str], radians: bool = False) -> list[dict]:
    """The beam's own end cuts, written again after the path reset that squares them.
    They read back in radians and are written in degrees up to Tapir 1.7.2."""
    def sent(value):
        return value if radians else round(math.degrees(value), 9) + 0.0
    return [{"name": name, "value": sent(float(current.get(name, 0.0)))}
            for name, _ in CUT_LIMITS.values() if name not in written]


def _kept_profiles(current: dict, n: int) -> tuple[list[int], int]:
    """A placed beam's segment profiles (slots 1 to 3) for a new path of n nodes,
    and how many segments on Profile 2 or 3 go back to Profile 1.

    The profiles stay while the node count does: padded with 1, values outside
    1 to 3 read as 1, and a beam from before profiles per segment reads as all 1.
    With another node count every segment goes back to Profile 1; the count
    leaves out the last item, which belongs to no segment."""
    old = current.get("segProfile") or []
    m = len(current.get("nodeX") or [])
    if m == n:
        kept = [int(v) if 1 <= int(v) <= 3 else 1 for v in old[:n]]
        return kept + [1] * (n - len(kept)), 0
    return [1] * n, sum(1 for v in old[:max(0, m - 1)] if int(v) in (2, 3))


def _ref_params(values: dict, keep_unset: bool) -> list[dict]:
    """Reference line placement. On an update, a value left as None is kept."""
    out = []
    line = values.get("ref_line")
    if line is None and not keep_unset:
        line = "centre"
    if line is not None:
        gdl = REF_LINES.get(str(line).strip().casefold())
        if gdl is None:
            raise SweptBeamError(f"ref_line must be one of 'left face', 'centre', 'right face', "
                                 f"got {line!r}.")
        out.append({"name": "refLine", "value": gdl})
    offset = values.get("ref_offset")
    if offset is None and not keep_unset:
        offset = 0.0
    if offset is not None:
        out.append({"name": "refOffset", "value": float(offset)})
    return out


def _node_params(local, arcs, tol: float, items: bool = False,
                 radians: bool = False, profiles: list[int] | None = None) -> list[dict]:
    """Node arrays and grip helpers for SetGDLParametersOfElements.

    Whole arrays (Tapir up to 1.7.1) take angle items in radians (single angles
    go in degrees; gate A). Every grip helper and its marker are written equal,
    and A equal to aDone, so the Parameter Script reads no pending grip drag or
    length edit (gate C).

    Item writes (Tapir 1.7.2 and newer) take angle items in degrees (radians from
    1.7.3) and follow pp.resize_writes, which leaves the add-node grips balanced,
    so the extension pairs are not written (a lone extEnd write would be a drag);
    x and y go in the order pp.parked_xy gives.

    profiles are the segment profile slots (all 1 when None, a new beam).
    """
    n = len(local)

    def r6(values):
        return [round(v, 6) + 0.0 for v in values]

    length = round(sum(sp.segment_plan_length(local[i], local[i + 1], arcs[i])
                       for i in range(n - 1)), 6)
    xs, ys = r6(p[0] for p in local), r6(p[1] for p in local)
    arc_values = [round(a if items and not radians else math.radians(a), 9) + 0.0 for a in arcs]
    if items:
        xy = pp.parked_xy(xs, ys)
        ext = []
    else:
        xy = [{"name": "nodeX", "value": xs}, {"name": "nodeY", "value": ys}]
        ext = [{"name": name, "value": 0.0}
               for name in ("extStart", "extStartDone", "extEnd", "extEndDone")]
    return xy + [
        {"name": "nodeZ", "value": r6(p[2] for p in local)},
        {"name": "nodeRoll", "value": [0.0] * n},
        {"name": "segArc", "value": arc_values},
        {"name": "segSag", "value": [0.0] * n},
        {"name": "sagDone", "value": [0.0] * n},
        {"name": "insL", "value": [0.0] * n},
        {"name": "insDone", "value": [0.0] * n},
        {"name": "segProfile", "value": list(profiles) if profiles else [1] * n},
        {"name": "segProfGrip", "value": [0.0] * n},
        {"name": "segProfDone", "value": [0.0] * n},
    ] + ext + [
        {"name": "pathTolerance", "value": tol},
        {"name": "A", "value": length},
        {"name": "aDone", "value": length},
    ]
