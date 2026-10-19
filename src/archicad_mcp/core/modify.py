"""Change existing elements: Tapir SetDetailsOfElements (story, layer, draw
order) and the 13 Modify commands, dry run by default.

The raw commands hide traps a caller cannot see from their schemas (read in
the Tapir source, 1.5.9 to 1.7.1; core.modify_fields has the tables): absolute
z writes that move an element to another story, partial objects that zero
their missing fields, a morph rotation about the wrong axis. Planning reads
every element first, so the dry run shows current -> new in the frame the
write uses, and names each element Archicad would refuse and why before
anything is sent. The design is docs/superpowers/specs/2026-10-19-modify-elements-design.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import jsonschema
from jsonschema.exceptions import best_match

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import modify_fields as mf
from archicad_mcp.core.attributes import layer_table, tapir_attributes
from archicad_mcp.core.editability import (
    LOCKED,
    NOT_FOUND,
    add_elsewhere,
    classify,
    lock_reasons,
    reason_text,
)
from archicad_mcp.core.element_data import cap_list
from archicad_mcp.core.mutate import _group
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK, element_payload
from archicad_mcp.gateway.registry import build_registry

RELEASES = "https://github.com/ENZYME-APD/tapir-archicad-automation/releases"
DETAIL_FIELDS = ["type", "id", "floorIndex", "layerIndex", "drawIndex", "details",
                 "hotlinkId"]


@dataclass
class Planned:
    guid: str
    type: str
    id: str | None
    entry: dict
    story: int | None = None
    layer: int | None = None
    draw_order: int | None = None
    keep_elevation: bool = False
    command: str | None = None
    item: dict = field(default_factory=dict)
    display: dict[str, dict] = field(default_factory=dict)
    expected: dict[str, object] = field(default_factory=dict)
    level_shift: float = 0.0


@dataclass
class Plan:
    requested: int
    planned: list[Planned] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    window: str | None = None
    stories: list[dict] = field(default_factory=list)
    suspend_groups: bool | None = None
    groups: dict[str, str] = field(default_factory=dict)
    tapir: tuple[int, int, int] = (0, 0, 0)


class _Skip(Exception):
    """One element's change cannot be sent; the message is the reason."""


def _merge_changes(changes) -> tuple[dict[str, dict], dict[str, str]] | str:
    """(GUID -> its fields, GUID -> conflict), or the error for a malformed call.

    GUIDs are uppercased: Tapir answers them uppercase, and every lookup by
    GUID after the first read would miss a lowercase one.
    """
    if not isinstance(changes, list):
        return "changes must be a list of {guid, ...fields} objects"
    merged: dict[str, dict] = {}
    conflicts: dict[str, str] = {}
    for number, change in enumerate(changes, 1):
        guid = change.get("guid") if isinstance(change, dict) else None
        if not isinstance(guid, str) or not guid.strip():
            return f"change {number} has no guid"
        guid = guid.strip().upper()
        target = merged.setdefault(guid, {})
        for key, value in change.items():
            if key == "guid":
                continue
            if key in target and target[key] != value:
                conflicts[guid] = f"'{key}' is given two different values"
            target[key] = value
    return merged, conflicts


def _version(conn: ArchicadConnection) -> tuple[int, int, int] | None:
    if not conn.tapir_available():
        return None
    raw = str(conn.tapir("GetAddOnVersion").get("version", ""))
    numbers = [int(n) for n in re.findall(r"\d+", raw)[:3]]
    return tuple((numbers + [0, 0, 0])[:3])


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(n) for n in version)


def _read_details(conn: ArchicadConnection, guids: list[str]) -> dict[str, dict]:
    """GUID -> GetDetailsOfElements item, for the elements the read finds."""
    found: dict[str, dict] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        items = conn.tapir("GetDetailsOfElements", {
            "elements": element_payload(chunk), "fields": DETAIL_FIELDS,
        }).get("detailsOfElements", [])
        for guid, item in zip(chunk, items):
            if isinstance(item, dict) and "error" not in item:
                found[guid] = item
    return found


def _match_name(name: str, items: list[dict], what: str) -> dict:
    """The one item called `name`: exact first, then trimmed and caseless."""
    exact = [i for i in items if i["name"] == name]
    if len(exact) == 1:
        return exact[0]
    wanted = name.strip().casefold()
    loose = [i for i in items if i["name"].strip().casefold() == wanted] if wanted else []
    if len(loose) == 1:
        return loose[0]
    if len(exact) + len(loose) > 1:
        raise _Skip(f"more than one {what} is named '{name}'")
    raise _Skip(f"no {what} named '{name}'")


class _Context:
    """What planning reads once per call: stories, layers, attributes by type."""

    def __init__(self, conn: ArchicadConnection, stories: list[dict],
                 layers: dict[int, dict], tapir: tuple[int, int, int]):
        self.conn = conn
        self.stories = stories
        self.levels = {s["index"]: float(s["level"]) for s in stories}
        self.layers = layers
        self.tapir = tapir
        self._attributes: dict[str, list[dict]] = {}

    def attributes(self, attribute_type: str) -> list[dict]:
        if attribute_type not in self._attributes:
            self._attributes[attribute_type] = tapir_attributes(self.conn, attribute_type)
        return self._attributes[attribute_type]

    def attribute_name(self, attribute_type: str, *, guid: str | None = None,
                       index: int | None = None):
        for a in self.attributes(attribute_type):
            if (guid is not None and a["guid"].upper() == guid.upper()) or \
                    (index is not None and a["index"] == index):
                return a["name"]
        return guid if guid is not None else index

    def story(self, value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise _Skip("story takes a story index or a story name")
        if isinstance(value, int):
            if value not in self.levels:
                indexes = sorted(self.levels)
                raise _Skip(f"no story {value}; the stories run from {indexes[0]} "
                            f"to {indexes[-1]}" if indexes else f"no story {value}")
            return value
        named = [{"name": s.get("name") or "", "index": s["index"]} for s in self.stories
                 if s.get("name")]
        return _match_name(value, named, "story")["index"]

    def layer(self, name) -> int:
        if not isinstance(name, str):
            raise _Skip("layer takes a layer name")
        items = [{"name": v["name"], "index": k} for k, v in self.layers.items()]
        return _match_name(name, items, "layer")["index"]


@lru_cache(maxsize=None)
def _validator(command: str):
    schema = mf.item_schema(command)
    return jsonschema.validators.validator_for(schema)(schema)


def _resolve(field_name: str, value, ctx: _Context):
    """(the value to send, the value to show) for attribute fields given by name."""
    if field_name in mf.ATTRIBUTE_FIELDS and isinstance(value, str):
        kind = mf.ATTRIBUTE_FIELDS[field_name]
        return {"guid": _match_name(value, ctx.attributes(kind), kind)["guid"]}, value
    if field_name in mf.INDEX_ATTRIBUTE_FIELDS and isinstance(value, str):
        kind = mf.INDEX_ATTRIBUTE_FIELDS[field_name]
        return _match_name(value, ctx.attributes(kind), kind)["index"], value
    if field_name in mf.SURFACE_OVERRIDES:
        if isinstance(value, str):
            guid = _match_name(value, ctx.attributes("Surface"), "Surface")["guid"]
            return {"overridden": True, "attributeId": {"guid": guid}}, value
        if isinstance(value, dict) and "attributeId" in value and "overridden" not in value:
            return {"overridden": True, **value}, value
    if field_name in mf.PEN_OVERRIDES and isinstance(value, dict) \
            and "penIndex" in value and "overridden" not in value:
        return {"overridden": True, **value}, value
    return value, value


def _shown_current(field_name: str, value, ctx: _Context):
    """A current value as the caller would name it: attributes by name."""
    if field_name in mf.ATTRIBUTE_FIELDS and isinstance(value, dict) and "guid" in value:
        return ctx.attribute_name(mf.ATTRIBUTE_FIELDS[field_name], guid=value["guid"])
    if field_name in mf.INDEX_ATTRIBUTE_FIELDS and isinstance(value, int):
        return ctx.attribute_name(mf.INDEX_ATTRIBUTE_FIELDS[field_name], index=value)
    if field_name in mf.SURFACE_OVERRIDES and isinstance(value, dict):
        attribute = value.get("attributeId") or {}
        if not value.get("overridden"):
            return "not overridden"
        return ctx.attribute_name("Surface", guid=attribute.get("guid", ""))
    return mf.summarize(value)


def _project(full, given):
    """`full` cut down to the keys `given` names, at every level."""
    if isinstance(full, dict) and isinstance(given, dict):
        return {k: _project(full.get(k), v) for k, v in given.items()}
    return full


def _shown_pair(current, new, given) -> tuple:
    """Current and new for a report. A large object (a fill, a text style)
    shows only the keys the caller gave, so the change stays visible; a small
    one (coordinates) shows whole, completed."""
    if isinstance(given, dict) and (len(json.dumps(current, default=str)) > 200
                                    or len(json.dumps(new, default=str)) > 200):
        return _project(current, given), _project(new, given)
    return current, new


def _plan_common(p: Planned, fields: dict, ctx: _Context) -> None:
    entry = p.entry
    if "story" in fields:
        if p.type in mf.HOSTED_TYPES:
            raise _Skip(f"a {p.type} lives in its host's story; change the host's "
                        "story instead")
        p.story = ctx.story(fields["story"])
        current = entry.get("floorIndex")
        p.level_shift = ctx.levels.get(p.story, 0.0) - ctx.levels.get(current, 0.0)
        p.display["story"] = {"current": current, "new": p.story}
        p.expected["story"] = p.story
    if "keep_elevation" in fields:
        keep = fields["keep_elevation"]
        if not isinstance(keep, bool):
            raise _Skip("keep_elevation takes true or false")
        if keep and "story" not in fields:
            raise _Skip("keep_elevation goes with a story change; give 'story' too")
        if keep and p.type in mf.FLAT_TYPES:
            raise _Skip(f"keep_elevation: a {p.type} has no elevation")
        p.keep_elevation = keep
    if "layer" in fields:
        p.layer = ctx.layer(fields["layer"])
        current = entry.get("layerIndex")
        p.display["layer"] = {"current": ctx.layers.get(current, {}).get("name", current),
                              "new": ctx.layers[p.layer]["name"]}
        p.expected["layer"] = p.layer
    if "draw_order" in fields:
        order = fields["draw_order"]
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 14:
            raise _Skip("draw_order takes an integer 0 to 14 (0 = the type's default)")
        p.draw_order = order
        p.display["draw_order"] = {"current": entry.get("drawIndex"), "new": order}
        if order:
            p.expected["draw_order"] = order


def _plan_typed(p: Planned, fields: dict, typed: dict, ctx: _Context) -> None:
    command = mf.TYPE_COMMANDS.get(p.type)
    if command is None:
        raise _Skip(f"a {p.type} takes only story, layer and draw_order here; no Modify "
                    "command covers its other fields")
    allowed = mf.allowed_fields(command)
    for name, value in typed.items():
        reason = mf.refusal(command, name, fields, p.entry, ctx.tapir)
        if reason is not None:
            raise _Skip(f"{name}: {reason}")
        if name not in allowed:
            raise _Skip(f"{command} has no field '{name}'; it takes: "
                        f"{', '.join(sorted(allowed))}")
        sent, shown = _resolve(name, value, ctx)
        sent = mf.complete(command, name, sent, p.entry)
        p.item[name] = sent
        current = mf.current_value(command, name, p.entry)
        # An attribute shows by the name it was given; anything else shows as
        # sent, completed, so it compares with the current value.
        new = shown if isinstance(shown, str) and shown is value else sent
        current, new = _shown_pair(current, new, value)
        p.display[name] = {"current": _shown_current(name, current, ctx),
                           "new": mf.summarize(new)}
        if mf.compared(command, name):
            p.expected[name] = mf.expected_read(command, name, sent, p.entry)
    error = best_match(_validator(command).iter_errors(p.item))
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        raise _Skip(f"{command} refuses '{path}': {error.message}" if path
                    else f"{command} refuses these fields: {error.message}")
    if not ctx.conn.tapir_command_available(command):
        since = build_registry()[command].version
        raise _Skip(f"the installed Tapir ({_version_text(ctx.tapir)}) has no {command}; "
                    f"it arrived in Tapir {since}")
    p.command = command


def _plan_element(guid: str, entry: dict, fields: dict, ctx: _Context) -> Planned:
    p = Planned(guid=guid, type=entry.get("type", ""), id=entry.get("id") or None,
                entry=entry)
    typed = {k: v for k, v in fields.items() if k not in mf.COMMON_FIELDS}
    _plan_common(p, fields, ctx)
    if typed:
        _plan_typed(p, fields, typed, ctx)
    return p


def _element_warnings(p: Planned, typed: dict, ctx: _Context) -> list[str]:
    out = []
    if p.story is not None and p.level_shift:
        if p.keep_elevation:
            out.append("stays at the same height; its offset from the story changes "
                       f"by {-p.level_shift:+.2f} m")
        else:
            out.append("keeps its height above the story, so it moves by "
                       f"{p.level_shift:+.2f} m")
    if p.command is not None:
        z_field = mf.ABSOLUTE_Z_FIELDS.get(p.command)
        z = mf.absolute_z(p.command, z_field, p.item.get(z_field)) if z_field else None
        if z is not None:
            old, new = p.entry.get("floorIndex"), mf.home_story(z, ctx.stories)
            if new != old:
                out.append(f"moves from story {old} to story {new}: Tapir takes the "
                           "story from the absolute z")
        out.extend(mf.side_effects(p.command, typed, p.entry))
    if p.draw_order is not None and p.type in mf.OPENING_TYPES:
        out.append("draw_order on a door or window goes through its wall: 0 resets the "
                   "wall and every opening in it, a level above 7 lifts every opening "
                   "in that wall, and 1 to 6 are unreachable")
    return out


def _groups(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    if not guids or not conn.tapir_command_available("GetGroupsOfElements"):
        return {}
    out: dict[str, str] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        items = conn.tapir("GetGroupsOfElements",
                           {"elements": element_payload(chunk)}).get("groupGuids", [])
        for guid, item in zip(chunk, items):
            group = (item or {}).get("groupId", {}).get("guid") if isinstance(item, dict) else None
            if group:
                out[guid] = group
    return out


def plan_changes(conn: ArchicadConnection, changes) -> Plan | dict:
    """Read, resolve and check every change; nothing is written.

    Returns a Plan, or {"error"} for a malformed call or a missing or old Tapir.
    """
    merged = _merge_changes(changes)
    if isinstance(merged, str):
        return {"error": merged}
    by_guid, conflicts = merged
    guids = list(by_guid)
    plan = Plan(requested=len(guids))
    if not guids:
        return plan
    tapir = _version(conn)
    if tapir is None:
        return {"error": "modify_elements needs the Tapir add-on (1.5.9 or newer), which "
                         f"this Archicad does not answer for. Releases: {RELEASES}"}
    if tapir < mf.MIN_TAPIR:
        return {"error": "modify_elements needs Tapir 1.5.9 or newer; this Archicad has "
                         f"{_version_text(tapir)}. Releases: {RELEASES}"}
    plan.tapir = tapir
    entries = _read_details(conn, guids)
    kinds, window = classify(conn, guids)
    # From the floor plan the details read finds other views' elements too.
    window = add_elsewhere(conn, [g for g in guids if g in entries and g not in kinds],
                           kinds, window)
    plan.window = window
    plan.stories = conn.tapir("GetStories").get("stories", [])
    ctx = _Context(conn, plan.stories, layer_table(conn), tapir)
    lockable = {g: entries[g] for g in guids
                if g in entries and kinds.get(g) in (None, LOCKED)}
    locks = lock_reasons(conn, lockable, ctx.layers) if lockable else {}

    typed_fields: dict[str, dict] = {}
    for guid in guids:
        fields = by_guid[guid]
        kind = kinds.get(guid)
        if guid not in entries or kind == NOT_FOUND:
            plan.skipped[guid] = reason_text(NOT_FOUND, window)
        elif kind not in (None, LOCKED):
            plan.skipped[guid] = reason_text(kind, window)
        elif guid in locks:
            plan.skipped[guid] = locks[guid]
        elif kind == LOCKED:
            plan.skipped[guid] = reason_text(LOCKED, window)
        elif guid in conflicts:
            plan.skipped[guid] = conflicts[guid]
        elif not set(fields) - {"keep_elevation"}:
            plan.skipped[guid] = "nothing to change: give a field besides guid"
        else:
            try:
                planned = _plan_element(guid, entries[guid], fields, ctx)
            except _Skip as skip:
                plan.skipped[guid] = str(skip)
                continue
            plan.planned.append(planned)
            typed_fields[guid] = {k: v for k, v in fields.items()
                                  if k not in mf.COMMON_FIELDS}

    for p in plan.planned:
        warnings = _element_warnings(p, typed_fields[p.guid], ctx)
        if warnings:
            plan.warnings[p.guid] = warnings
    plan.groups = _groups(conn, [p.guid for p in plan.planned])
    if plan.groups:
        plan.suspend_groups = bool(conn.tapir("GetSuspendGroupsMode").get("suspendGroups"))
        state = "on" if plan.suspend_groups else "off"
        for guid in plan.groups:
            plan.warnings.setdefault(guid, []).append(
                f"is in a group (Suspend Groups is {state}); only this element is sent, "
                "its group mates are not part of the request")
    return plan


def _grouped_warnings(warnings: dict[str, list[str]]) -> list[dict]:
    by_text: dict[str, list[str]] = {}
    for guid, texts in warnings.items():
        for text in texts:
            by_text.setdefault(text, []).append(guid)
    out = []
    for text, guids in sorted(by_text.items(), key=lambda item: -len(item[1])):
        shown, rest = cap_list(guids)
        group = {"warning": text, "count": len(guids), "guids": shown}
        if rest:
            group["not_shown"] = rest
        out.append(group)
    return out


def _view(p: Planned) -> dict:
    view = {"guid": p.guid, "type": p.type}
    if p.id:
        view["id"] = p.id
    view["changes"] = p.display
    return view


def report_dry_run(plan: Plan) -> dict:
    out: dict = {"dry_run": True, "requested": plan.requested,
                 "planned_count": len(plan.planned)}
    shown, rest = cap_list(plan.planned)
    if shown:
        out["planned"] = [_view(p) for p in shown]
    if rest:
        out["not_shown"] = rest
    if plan.skipped:
        out["skipped"] = _group(plan.skipped)
    if plan.warnings:
        out["warnings"] = _grouped_warnings(plan.warnings)
    if plan.window is not None:
        out["active_window"] = plan.window
    return out


def modify_elements(conn: ArchicadConnection, changes, dry_run: bool = True) -> dict:
    plan = plan_changes(conn, changes)
    if isinstance(plan, dict):
        return plan
    if dry_run:
        return report_dry_run(plan)
    from archicad_mcp.core.modify_commit import commit  # imports this module
    return commit(conn, plan)
