"""edit_attributes: create, modify and delete attributes, and set their
property values, over Tapir's Create* attribute commands, DeleteAttributes and
Get/SetPropertyValuesOfAttributes.

Tapir's Create* commands create OR overwrite, and an overwrite keeps only what
Tapir happens to keep (docs/superpowers/specs/2026-10-19-edit-attributes-design.md,
"What the Tapir source says"). So a `create` is always sent with
overwriteExisting off and can never replace anything, a `modify` names the
attribute it changes and is sent with what that type's overwrite would
otherwise lose, and values Tapir would silently replace with a default are
refused here instead.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import jsonschema
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.attribute_types import (
    ENUMS,
    LAYER_LIST,
    REFERENCE_FIELDS,
    TYPES,
    WRITE_ORDER,
    AttrType,
    lookup_type,
)
from archicad_mcp.core.attributes import (
    AttributeNames,
    _guid_of,
    named_references,
    read_records,
)
from archicad_mcp.core.definition_edit import Definitions, group_by_error
from archicad_mcp.core.element_data import error_fields

TOOL = "edit_attributes"
ACTIONS = ("create", "modify", "delete")
PENDING = "00000000-0000-0000-0000-000000000000"
PROBLEMS_PER_CHANGE = 3
DETAIL_CHUNK = 500
FLOOR_PLAN = "FloorPlan"

# Index 1 of these is Archicad's own: it refuses Modify and Delete.
_BUILTIN_INDEX_TYPES = frozenset({"Layer", "Line", "Fill"})
_BUILTIN_FILL_TYPES = frozenset({"Solid", "Empty"})
_LAYER_FLAGS = frozenset({"isHidden", "isLocked", "isWireframe"})
_LAYER_DEFAULT = {"isHidden": False, "isLocked": False, "isWireframe": False,
                  "intersectionGroupNr": 1}
_LINE_PATTERN = ("period", "height", "dashItems", "lineItems")
_STAMP = ("stampName", "stampMainGuid", "stampRevGuid")
# What a modify sends besides the fields asked for, per type (Tapir 1.7.1 source).
_ALWAYS_RESENT = {"Composite": ("useWith", "skins", "separators"),
                  "BuildingMaterial": ("id", "manufacturer", "description"),
                  "LayerCombination": ("layers",)}
_PROPERTY_COMMANDS = ("GetPropertyValuesOfAttributes", "SetPropertyValuesOfAttributes")

_DELETE_WARNING = ("elements and attributes using it keep pointing at a missing "
                   "attribute, and a delete cannot be undone")
_LAYER_DELETE_WARNING = ("deleting a layer deletes every element on it, in every view: "
                         "only the floor plan was counted, so elements on it in sections, "
                         "elevations, interior elevations, details, worksheets and "
                         "layouts would go too; it cannot be undone")


# ---------- schemas ----------

def _item_schema(attr: AttrType) -> dict:
    from archicad_mcp.gateway.registry import build_registry
    schema = build_registry()[attr.create].input_schema or {}
    return schema.get("properties", {}).get(attr.array_key, {}).get("items", {})


def _settable(attr: AttrType) -> list[str]:
    return [k for k in _item_schema(attr).get("properties", {})
            if k not in ("attributeId", "index")]


def _name_fields(schema) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, sub in schema.get("properties", {}).items():
            if key in REFERENCE_FIELDS:
                found.add(key)
            found |= _name_fields(sub)
        if "items" in schema:
            found |= _name_fields(schema["items"])
    return found


def field_lists() -> dict:
    """What `changes=[]` answers: every type's fields and their rules."""
    types = {}
    for attr in TYPES.values():
        schema = _item_schema(attr)
        names = sorted(_name_fields(schema))
        if attr.name == "LayerCombination":
            names.append("layers[].attributeId")
        types[attr.name] = {"fields": _settable(attr), "name_fields": names,
                            "refused_in_modify": dict(attr.refused_in_modify),
                            "create_requires": ["name", *attr.create_requires]}
    return {"types": types,
            "note": "Fields use Tapir's names and units (metres, radians, pen numbers, "
                    "colours 0 to 1). name_fields take another attribute's name. "
                    "list_attributes(details=true) shows current values in this form."}


# ---------- plans ----------

@dataclass
class Plan:
    action: str
    type_name: str | None
    target: str
    attr: AttrType | None = None
    guid: str | None = None
    index: int | None = None
    asked: dict = field(default_factory=dict)       # fields as asked, references by name
    expected: dict = field(default_factory=dict)    # asked keys after merging, by name
    named: dict = field(default_factory=dict)       # the item to send, references by name
    props: list[dict] = field(default_factory=list)  # {address, guid, value, current}
    changes: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    would_change: dict | None = None
    set_name: str | None = None
    advisory_set: bool = False                       # a missing reservation only warns
    send_fields: bool = False

    def head(self) -> dict:
        return {"action": self.action, "type": self.type_name, "target": self.target}

    def entry(self) -> dict:
        out = self.head()
        if self.action == "create" and not self.errors:
            out["fields"] = self.asked
        if self.changes:
            out["changes"] = self.changes
        if self.warnings:
            out["warnings"] = self.warnings
        if self.errors:
            out["errors"] = self.errors
        if self.would_change is not None:
            out["would_change"] = self.would_change
        return out


class _Context:
    def __init__(self, conn: ArchicadConnection):
        self.conn = conn
        self.names = AttributeNames(conn)
        self.creating: dict[str, set[str]] = {}
        # (type, old or new name) -> GUID for renames in this call, so a reference
        # by either name still finds the attribute after the rename is sent.
        self.renamed: dict[tuple[str, str], str] = {}
        # (type, old name) -> new name, to compare a read-back that names the
        # attribute by its new name.
        self.rename_to: dict[tuple[str, str], str] = {}
        self._records: dict[tuple[str, str], dict | None] = {}
        self._available: dict[str, str | None] = {}
        self._defs: Definitions | None = None
        self._layer_counts: dict[int, int] | None = None
        self.unreadable = 0
        # (type, normalised name) -> what claims it in this call (a create or a rename)
        self.claimed: dict[tuple[str, str], str] = {}
        self._window: str | None = None
        self._window_read = False

    def refusal(self, command: str) -> str | None:
        if command not in self._available:
            from archicad_mcp.tapir_requirements import require_tapir_command
            refused = require_tapir_command(self.conn, command, TOOL)
            self._available[command] = refused["error"] if refused else None
        return self._available[command]

    def record(self, attr: AttrType, guid: str) -> dict | None:
        key = (attr.name, guid)
        if key not in self._records:
            self._records[key] = read_records(self.conn, attr, [guid])[0]
        return self._records[key]

    def definitions(self) -> Definitions:
        if self._defs is None:
            self._defs = Definitions.load(self.conn)
        return self._defs

    def window(self) -> str | None:
        if not self._window_read:
            from archicad_mcp.core.editability import active_window
            self._window = active_window(self.conn)
            self._window_read = True
        return self._window

    def layer_counts(self) -> dict[int, int]:
        if self._layer_counts is None:
            from archicad_mcp.extract import element_payload
            guids = [e["elementId"]["guid"] for e in
                     self.conn.tapir("GetAllElements").get("elements", [])]
            counts: dict[int, int] = {}
            for start in range(0, len(guids), DETAIL_CHUNK):
                for item in self._layer_details(guids[start:start + DETAIL_CHUNK],
                                                element_payload):
                    index = item.get("layerIndex") if isinstance(item, dict) else None
                    if isinstance(index, int) and not isinstance(index, bool):
                        counts[index] = counts.get(index, 0) + 1
                    else:
                        # Its layer is unknown, so it could be on the layer to delete.
                        self.unreadable += 1
            self._layer_counts = counts
        return self._layer_counts

    def _layer_details(self, chunk: list[str], payload) -> list:
        """One answer per element, None where it could not be read. One element
        that fails Tapir's answer schema sinks its whole batch, so a failing chunk
        is retried in pieces of 25 and then one by one."""
        try:
            items = self.conn.tapir("GetDetailsOfElements", {
                "elements": payload(chunk), "fields": ["layerIndex"]}).get(
                "detailsOfElements", [])
        except APIErrorBase:
            if len(chunk) == 1:
                return [None]
            size = 25 if len(chunk) > 25 else 1
            out: list = []
            for start in range(0, len(chunk), size):
                out += self._layer_details(chunk[start:start + size], payload)
            return out
        items = [i if isinstance(i, dict) and "error" not in i else None for i in items]
        return items + [None] * (len(chunk) - len(items))


def _norm(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def _near(ctx: _Context, type_name: str, name: str) -> list[str]:
    key = _norm(name)
    return [h["name"] for h in ctx.names.headers(type_name)
            if h["name"] != name and _norm(h["name"]) == key]


def _resolve_attribute(ctx: _Context, attr: AttrType, ref) -> tuple[dict | None, str | None]:
    if isinstance(ref, str):
        header = ctx.names.by_name(attr.name, ref) or ctx.names.by_guid(attr.name, ref)
        if header:
            return header, None
        near = _near(ctx, attr.name, ref)
        hint = f"; did you mean {', '.join(repr(n) for n in near)}?" if near else ""
        return None, f"no {attr.name} named '{ref}'{hint}"
    return None, "'attribute' takes the attribute's name or GUID"


def _plan(ctx: _Context, change, position: int) -> Plan:
    if not isinstance(change, dict):
        return Plan("?", None, f"change {position}", errors=["a change is an object"])
    actions = [a for a in ACTIONS if a in change]
    label = str(change.get("name") or change.get("attribute") or f"change {position}")
    if len(actions) != 1:
        return Plan("?", None, label, errors=[
            "give exactly one of 'create', 'modify' or 'delete', whose value is the "
            "attribute type"])
    action = actions[0]
    attr = lookup_type(change[action])
    if attr is None:
        return Plan(action, None, label, errors=[
            f"Unknown attribute type '{change[action]}'. Valid: {sorted(TYPES)}."])
    plan = Plan(action, attr.name, label, attr=attr, set_name=attr.teamwork_set)
    if action == "create":
        _plan_create(ctx, plan, change)
    elif action == "modify":
        _plan_modify(ctx, plan, change)
    else:
        _plan_delete(ctx, plan, change)
    return plan


def _fields_of(plan: Plan, change: dict, skip: tuple[str, ...]) -> dict | None:
    fields = {k: v for k, v in change.items() if k not in skip}
    allowed = _settable(plan.attr)
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown} for {plan.type_name}; allowed: "
                           f"{allowed}")
        return None
    return fields


def _need(ctx: _Context, plan: Plan, *commands: str) -> bool:
    for command in commands:
        refused = ctx.refusal(command)
        if refused:
            plan.errors.append(refused)
            return False
    return True


# ---------- create ----------

def _zero_starts(type_name: str, fields: dict) -> list[str]:
    """Tapir builds a new attribute from an empty one, so what is not given starts
    at zero, not at Archicad's defaults (live 19.10.2026: a new building material
    had cut surface index 0, which is no surface)."""
    if type_name == "Layer" and "intersectionGroupNr" not in fields:
        return ["intersectionGroupNr is not given and starts at 0, not Archicad's usual 1"]
    if type_name == "BuildingMaterial":
        missing = [k for k in ("cutFillIndex", "cutSurfaceIndex") if k not in fields]
        if missing:
            return [f"{' and '.join(missing)} not given: they start at index 0, which is no "
                    "attribute"]
    return []


def _claimed(ctx: _Context, type_name: str, name: str, plan: Plan) -> bool:
    """True (with the error) when another change in this call already creates or
    renames to this name, ignoring case and spaces: Tapir does not check."""
    other = ctx.claimed.get((type_name, _norm(name)))
    if other:
        plan.errors.append(f"{type_name} '{name}' clashes with {other} in this call")
        return True
    return False


def _plan_create(ctx: _Context, plan: Plan, change: dict) -> None:
    attr = plan.attr
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        plan.errors.append(f"a create needs a name for the new {attr.name}")
        return
    plan.target = name
    fields = _fields_of(plan, change, ("create", "name"))
    if fields is None or not _need(ctx, plan, attr.create):
        return
    plan.asked = named_references(fields, attr.name, ctx.names)
    existing = ctx.names.by_name(attr.name, name)
    if existing is not None:
        plan.errors.append(
            f"{attr.name} '{name}' already exists; to change it send "
            f'{{"modify": "{attr.name}", "attribute": "{name}", ...}}')
        if ctx.refusal(attr.get) is None:
            current = ctx.record(attr, existing["guid"])
            if current is not None:
                plan.would_change = _diff(named_references(current, attr.name, ctx.names),
                                          plan.asked)
        return
    if _claimed(ctx, attr.name, name, plan):
        return
    near = _near(ctx, attr.name, name)
    if near:
        plan.warnings.append(
            f"{attr.name} {', '.join(repr(n) for n in near)} already exists; this creates "
            "a second, nearly identical name")
    plan.warnings.extend(_zero_starts(attr.name, fields))
    missing = [k for k in attr.create_requires if k not in fields]
    if missing:
        plan.errors.append(f"a new {attr.name} needs {missing}"
                           + (": without useWith no element type could use it"
                              if "useWith" in missing else ""))
    if attr.name == "Profile" and not ({"sourceAttributeId", "newSkins"} & set(fields)):
        plan.errors.append("a new Profile needs sourceAttributeId or newSkins (its geometry)")
    plan.expected = dict(plan.asked)
    plan.named = {"name": name, **copy.deepcopy(plan.asked)}
    plan.send_fields = True
    ctx.creating.setdefault(attr.name, set()).add(name)
    ctx.claimed[(attr.name, _norm(name))] = f"the create of '{name}'"


# ---------- modify ----------

def _plan_modify(ctx: _Context, plan: Plan, change: dict) -> None:
    attr = plan.attr
    if "attribute" not in change:
        plan.errors.append("a modify needs 'attribute': the name or GUID of the "
                           f"{attr.name} to change")
        return
    header, err = _resolve_attribute(ctx, attr, change["attribute"])
    if err:
        plan.errors.append(err)
        return
    plan.target, plan.guid, plan.index = header["name"], header["guid"], header["index"]
    fields = _fields_of(plan, change, ("modify", "attribute", "properties"))
    if fields is None:
        return
    properties = change.get("properties")
    if not fields and not properties:
        plan.errors.append("nothing asked: give fields to change or 'properties'")
        return
    if fields:
        _plan_modify_fields(ctx, plan, fields)
    if properties is not None and not plan.errors:
        _plan_properties(ctx, plan, properties)
    if not plan.errors and not plan.changes:
        plan.warnings.append("nothing to change")
        plan.send_fields = False


def _builtin_reason(attr: AttrType, header_index: int, record: dict | None,
                    target: str) -> str | None:
    if attr.name in _BUILTIN_INDEX_TYPES and header_index == 1:
        return (f"{attr.name} index 1 ('{target}') is Archicad's own and cannot be "
                "changed or deleted")
    if attr.name == "Fill" and record and record.get("subType") in _BUILTIN_FILL_TYPES:
        return f"the {record['subType']} fill is Archicad's own and cannot be changed or deleted"
    return None


def _plan_modify_fields(ctx: _Context, plan: Plan, fields: dict) -> None:
    attr = plan.attr
    if not _need(ctx, plan, attr.create, attr.get):
        return
    record = ctx.record(attr, plan.guid)
    if record is None:
        plan.errors.append(f"Archicad did not return {attr.name} '{plan.target}'")
        return
    builtin = _builtin_reason(attr, plan.index, record, plan.target)
    if builtin:
        plan.errors.append(builtin)
        return
    refused = [f"{k}: {why}" for k, why in attr.refused_in_modify.items() if k in fields]
    if refused:
        plan.errors.extend(refused)
        return
    current = {k: v for k, v in named_references(record, attr.name, ctx.names).items()
               if k not in ("attributeId", "index")}
    if attr.name == "LayerCombination" and isinstance(current.get(LAYER_LIST), list):
        layers = current[LAYER_LIST]
        current[LAYER_LIST] = [e for e in layers
                               if not str(e.get("attributeId", "")).startswith("<missing")]
        stale = len(layers) - len(current[LAYER_LIST])
        if stale:
            plan.warnings.append(f"{stale} layer entries point at layers that no longer exist "
                                 "and are left out (Tapir drops them anyway)")
    asked = named_references(fields, attr.name, ctx.names)
    if "name" in asked and (not isinstance(asked["name"], str) or not asked["name"].strip()):
        plan.errors.append("name must be a non-empty text")
        return
    if "name" in asked and asked["name"] != plan.target:
        if _claimed(ctx, attr.name, asked["name"], plan):
            return
        clash = ctx.names.by_name(attr.name, asked["name"]) or (
            [n for n in _near(ctx, attr.name, asked["name"]) if n != plan.target] or None)
        if clash:
            plan.errors.append(f"{attr.name} '{asked['name']}' already exists (or a name "
                               "differing only in case or spaces does)")
            return
    merged, err = _merge(attr, current, asked)
    if err:
        plan.errors.append(err)
        return
    if attr.name == "Line" and "period" in asked and "dashItems" not in asked \
            and merged.get("lineType", current.get("lineType")) == "Dashed":
        plan.errors.append("a Dashed line's period follows its dash items (live: period 3 "
                           "read back as 2, their sum); send dashItems instead")
        return
    plan.asked = asked
    plan.expected = merged
    plan.changes = _diff(current, merged)
    if attr.name == "Profile" and "skinOverrides" in asked:
        err = _skin_override_changes(current, asked["skinOverrides"], plan.changes)
        if err:
            plan.errors.append(err)
            return
    named = {"name": merged.get("name", plan.target), **merged}
    for key in _ALWAYS_RESENT.get(attr.name, ()):
        if key not in named and key in current:
            named[key] = copy.deepcopy(current[key])
    if attr.name == "Line" and any(k in asked for k in _LINE_PATTERN) \
            and "lineType" not in named and "lineType" in current:
        named["lineType"] = current["lineType"]
    if attr.name == "ZoneCategory" and any(k in asked for k in _STAMP):
        for key in _STAMP:
            if key not in named and key in current:
                named[key] = current[key]
    if attr.name == "Composite":
        bare = [i for i, skin in enumerate(named.get("skins", []))
                if isinstance(skin, dict) and "buildingMaterialId" not in skin]
        if bare:
            plan.errors.append(f"skin(s) {bare} of this composite have no building material "
                               "Archicad can resolve (deleted?); send skins with a "
                               "buildingMaterialId for them")
            return
    plan.named = named
    plan.send_fields = bool(plan.changes)
    if "name" in plan.changes:
        ctx.renamed[(attr.name, plan.target)] = plan.guid
        ctx.renamed[(attr.name, asked["name"])] = plan.guid
        ctx.rename_to[(attr.name, plan.target)] = asked["name"]
        ctx.claimed[(attr.name, _norm(asked["name"]))] = f"the rename of '{plan.target}'"
    plan.advisory_set = attr.name == "Layer" and set(asked) <= _LAYER_FLAGS


def _skin_override_changes(current: dict, overrides, changes: dict) -> str | None:
    """A profile's skinOverrides diffed against the skins they name, by skinId."""
    if not isinstance(overrides, list):
        return "skinOverrides takes a list of {skinId, fields...}"
    skins = {s.get("skinId"): s for s in current.get("skins", []) if isinstance(s, dict)}
    for key in [k for k in changes if k.startswith("skinOverrides")]:
        del changes[key]
    for override in overrides:
        skin_id = override.get("skinId") if isinstance(override, dict) else None
        if skin_id not in skins:
            return f"no skin '{skin_id}' in this profile; its skins: {sorted(skins)}"
        _diff(skins[skin_id], {k: v for k, v in override.items() if k != "skinId"},
              f"skins[{skin_id}]", changes)
    return None


def _whole(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _merge(attr: AttrType, current: dict, asked: dict) -> tuple[dict, str | None]:
    merged: dict = {}
    for key, value in asked.items():
        now = current.get(key)
        if attr.name == "Composite" and key in ("skins", "separators") \
                and isinstance(value, list):
            now = now or []
            merged[key] = [{**now[i], **item} if i < len(now) and isinstance(item, dict)
                           else item for i, item in enumerate(value)]
        elif attr.name == "LayerCombination" and key == LAYER_LIST and isinstance(value, list):
            layers = {e["attributeId"]: dict(e) for e in now or []}
            for item in value:
                if not isinstance(item, dict) or not isinstance(item.get("attributeId"), str):
                    return {}, "each of layers takes {attributeId: <layer name>, flags...}"
                base = layers.get(item["attributeId"], {"attributeId": item["attributeId"],
                                                        **_LAYER_DEFAULT})
                layers[item["attributeId"]] = {**base, **item}
            merged[key] = list(layers.values())
        elif attr.name == "PenTable" and key == "pens" and isinstance(value, list):
            if not value:
                continue
            pens = {p.get("index"): p for p in now or []}
            value = [{**p, "index": _whole(p.get("index"))} if isinstance(p, dict) else p
                     for p in value]
            merged[key] = [{**pens.get(p.get("index"), {}), **p} if isinstance(p, dict) else p
                           for p in value]
        elif attr.name == "Surface" and key == "texture" and isinstance(value, dict):
            texture = now or {}
            off = [k for k, v in value.items()
                   if v is False and texture.get(k) is True]
            if off:
                return {}, (f"texture {off} cannot be switched off: Tapir can only switch "
                            "texture flags on")
            merged[key] = {**texture, **value}
        elif isinstance(value, dict) and isinstance(now, dict):
            merged[key] = {**now, **value}
        else:
            merged[key] = value
    if attr.name == "Composite":
        skins = merged.get("skins", current.get("skins", []))
        separators = merged.get("separators", current.get("separators", []))
        if len(separators) != len(skins) + 1:
            return {}, (f"a composite with {len(skins)} skins needs {len(skins) + 1} "
                        f"separators; send separators too")
    return merged, None


# ---------- properties ----------

def _plan_properties(ctx: _Context, plan: Plan, properties) -> None:
    if not isinstance(properties, dict) or not properties:
        plan.errors.append("'properties' takes an object of 'Group/Name' to value")
        return
    if not _need(ctx, plan, *_PROPERTY_COMMANDS):
        return
    defs = ctx.definitions()
    wanted = []
    for address, value in properties.items():
        definition, err = defs.resolve(address)
        if definition is None:
            plan.errors.append(err)
            continue
        if definition.builtin:
            plan.errors.append(f"'{address}' is a built-in property; only custom ones are "
                               "written")
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            plan.errors.append(f"'{address}' takes text or a number, got {value!r}")
            continue
        wanted.append({"address": address, "guid": definition.guid,
                       "value": value if isinstance(value, str) else repr(value),
                       "compare": definition.value_type == "String"})
    if plan.errors or not wanted:
        return
    answer = ctx.conn.tapir("GetPropertyValuesOfAttributes", {
        "attributeIds": [{"attributeId": {"guid": plan.guid}}],
        "properties": [{"propertyId": {"guid": w["guid"]}} for w in wanted]})
    rows = (answer.get("propertyValuesForAttributes") or [{}])[0]
    values = rows.get("propertyValues", []) if isinstance(rows, dict) else []
    diffs = {}
    for i, w in enumerate(wanted):
        cell = values[i] if i < len(values) else {"error": {}}
        if not isinstance(cell, dict) or "error" in cell:
            plan.errors.append(f"property '{w['address']}' is not available for "
                               f"{plan.type_name} '{plan.target}'")
            continue
        w["current"] = cell.get("propertyValue", {}).get("value")
        if not w["compare"] or w["current"] != w["value"]:
            diffs[w["address"]] = [w["current"], w["value"]]
            plan.props.append(w)
    if diffs:
        plan.changes["properties"] = diffs


# ---------- delete ----------

def _plan_delete(ctx: _Context, plan: Plan, change: dict) -> None:
    attr = plan.attr
    extra = sorted(set(change) - {"delete", "attribute"})
    if extra:
        plan.errors.append(f"a delete takes only 'delete' and 'attribute', not {extra}")
        return
    if "attribute" not in change:
        plan.errors.append(f"a delete needs 'attribute': the name or GUID of the {attr.name}")
        return
    header, err = _resolve_attribute(ctx, attr, change["attribute"])
    if err:
        plan.errors.append(err)
        return
    plan.target, plan.guid, plan.index = header["name"], header["guid"], header["index"]
    if not _need(ctx, plan, "DeleteAttributes"):
        return
    record = None
    if attr.name == "Fill" and _need(ctx, plan, attr.get):
        record = ctx.record(attr, plan.guid)
    builtin = _builtin_reason(attr, plan.index, record, plan.target)
    if builtin:
        plan.errors.append(builtin)
        return
    if attr.name == "Composite" and len(ctx.names.headers(attr.name)) <= 1:
        plan.errors.append("Archicad keeps at least one composite; this is the last one")
        return
    if attr.name == "Layer":
        if ctx.window() != FLOOR_PLAN:
            plan.errors.append(
                "a layer delete is checked only with the floor plan active (it deletes "
                "the layer's elements everywhere, and only the active view can be "
                "counted); switch to the floor plan and retry")
            return
        count = ctx.layer_counts().get(plan.index, 0)
        if count:
            plan.errors.append(
                f"{count} elements are on layer '{plan.target}' and would be deleted with "
                "it; move them (modify_elements) or delete them (delete_elements) first")
            return
        if ctx.unreadable:
            plan.errors.append(
                f"{ctx.unreadable} elements could not be read, so their layer is unknown and "
                f"they could be on layer '{plan.target}'; nothing is deleted")
            return
        plan.warnings.append(_LAYER_DELETE_WARNING)
    else:
        plan.warnings.append(_DELETE_WARNING)


# ---------- references, values, schema ----------

def _to_tapir(ctx: _Context, plan: Plan, pending_ok: bool) -> tuple[dict, list[str]]:
    errors: list[str] = []
    item = _tapir_value(ctx, plan.named, plan.type_name, errors, pending_ok, False)
    if plan.action == "modify":
        item = {"attributeId": {"guid": plan.guid}, **item}
    return item, errors


def _tapir_value(ctx, value, type_name, errors, pending_ok, in_layer_list):
    if isinstance(value, list):
        return [_tapir_value(ctx, v, type_name, errors, pending_ok, in_layer_list)
                for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in REFERENCE_FIELDS:
            ref_type, form = REFERENCE_FIELDS[key]
            # A source is copied when its own type's batch is sent: one created in
            # the same batch is not there yet.
            out[key] = _reference(ctx, ref_type or type_name, form, item, errors,
                                  pending_ok and ref_type is not None)
        elif in_layer_list and key == "attributeId":
            out[key] = _reference(ctx, "Layer", "layer", item, errors, pending_ok)
        else:
            out[key] = _tapir_value(ctx, item, type_name, errors, pending_ok,
                                    type_name == "LayerCombination" and key == LAYER_LIST)
    return out


def _reference(ctx: _Context, ref_type: str, form: str, value, errors: list[str],
               pending_ok: bool):
    header = None
    if isinstance(value, str):
        header = ctx.names.by_name(ref_type, value) or ctx.names.by_guid(ref_type, value)
        if header is None and (ref_type, value) in ctx.renamed:
            header = ctx.names.by_guid(ref_type, ctx.renamed[(ref_type, value)])
        if header is None and pending_ok and value in ctx.creating.get(ref_type, set()):
            header = {"guid": PENDING, "index": 0}
        if header is None and value in ctx.creating.get(ref_type, set()):
            errors.append(f"'{value}' is created in this same call, and a {ref_type} named "
                          "here must exist before the call; create it first")
            return value
        if header is None:
            near = _near(ctx, ref_type, value)
            hint = f" (did you mean {', '.join(repr(n) for n in near)}?)" if near else ""
            errors.append(f"no {ref_type} named '{value}'{hint}")
            return value
    elif form == "index" and isinstance(value, int) and not isinstance(value, bool):
        header = ctx.names.by_index(ref_type, value)
        if header is None:
            errors.append(f"no {ref_type} with index {value}")
            return value
    else:
        guid = _guid_of(value)
        header = ctx.names.by_guid(ref_type, guid) if guid else None
        if header is None:
            errors.append(f"{value!r} is not a {ref_type} of this project; give its name")
            return value
    if form == "index":
        return header["index"]
    if form == "layer":
        return {"guid": header["guid"]}
    return {"attributeId": {"guid": header["guid"]}}


def _values_at(obj, path: str) -> list:
    """Values at an ENUMS path ("skins[].type", "useWith[]", "lineType")."""
    values = [obj]
    for part in path.split("."):
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        nxt = []
        for v in values:
            if isinstance(v, dict) and key in v:
                got = v[key]
                if is_list:
                    nxt.extend(got if isinstance(got, list) else [])
                else:
                    nxt.append(got)
        values = nxt
    return values


def _enum_errors(type_name: str, item: dict) -> list[str]:
    errors = []
    for (enum_type, path), allowed in ENUMS.items():
        if enum_type != type_name:
            continue
        for value in _values_at(item, path):
            if value not in allowed:
                field_name = path.replace("[]", "")
                errors.append(f"{field_name} '{value}' is not one of {list(allowed)} "
                              "(Tapir would silently use a default)")
    return errors


def _integers(value, schema):
    """Whole-number floats as ints where the schema wants an integer: Tapir reads
    pens into a short, which ignores a float."""
    if not isinstance(schema, dict):
        return value
    if schema.get("type") == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: _integers(v, props.get(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_integers(v, schema.get("items")) for v in value]
    return value


def _schema_errors(attr: AttrType, item: dict) -> list[str]:
    schema = _item_schema(attr)
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(item), key=jsonschema.exceptions.relevance)
    out = []
    for error in errors[:PROBLEMS_PER_CHANGE]:
        path = "/".join(str(p) for p in error.absolute_path)
        out.append(f"{path or 'item'}: {error.message}")
    return out


def _one_change_per_attribute(plans: list[Plan]) -> None:
    """Every payload is built from the state before the call, so a second change
    of the same attribute would resend what the first one changed."""
    seen: dict[tuple[str, str], int] = {}
    for position, p in enumerate(plans):
        if p.errors or p.guid is None:
            continue
        key = (p.type_name, p.guid)
        if key in seen:
            p.errors.append(f"{p.type_name} '{p.target}' is already changed by change "
                            f"{seen[key]}; put everything for one attribute in one change")
        else:
            seen[key] = position


def _check_payloads(ctx: _Context, plans: list[Plan]) -> None:
    """References to an attribute created in the same call count only while its
    create is still good, so creates are checked first, in write order (a type
    before the types that refer to it), and drop out of `creating` on failure."""
    creates = [p for p in plans if p.action == "create"]
    ctx.creating = {}
    for p in creates:
        if not p.errors:
            ctx.creating.setdefault(p.type_name, set()).add(p.target)
    for type_name in WRITE_ORDER:
        for p in creates:
            if p.type_name == type_name:
                _check_payload(ctx, p)
                if p.errors:
                    ctx.creating.get(type_name, set()).discard(p.target)
    for p in plans:
        if p.action != "create":
            _check_payload(ctx, p)


def _check_payload(ctx: _Context, plan: Plan) -> None:
    if not plan.send_fields or plan.errors:
        return
    enum_errors = _enum_errors(plan.type_name, plan.named)
    if enum_errors:
        plan.errors.extend(enum_errors)
        return
    item, errors = _to_tapir(ctx, plan, pending_ok=True)
    if errors:
        plan.errors.extend(errors)
        return
    item = _integers(item, _item_schema(plan.attr))
    plan.errors.extend(_schema_errors(plan.attr, item))


# ---------- diffs ----------

def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _keyed(path: str, items: list) -> dict | None:
    """Pens by index and a layer combination's layers by name diff by key."""
    if path.endswith("pens") and all(isinstance(i, dict) and "index" in i for i in items):
        return {f"[{_whole(i['index'])}]": i for i in items}
    if path.endswith(LAYER_LIST) and all(isinstance(i, dict) and "attributeId" in i
                                         for i in items):
        return {f"[{i['attributeId']}]": i for i in items}
    return None


def _join(path: str, key: str) -> str:
    return f"{path}{key}" if key.startswith("[") else (f"{path}.{key}" if path else key)


def _diff(current, new, path: str = "", out: dict | None = None) -> dict:
    """Every path where `new` differs from `current`, as {path: [current, new]};
    only the keys `new` names are compared."""
    out = {} if out is None else out
    if isinstance(new, dict) and isinstance(current, dict):
        for key, value in new.items():
            _diff(current.get(key), value, _join(path, key), out)
    elif isinstance(new, list) and isinstance(current, list) and new \
            and all(isinstance(v, dict) for v in new):
        keyed_new, keyed_now = _keyed(path, new), _keyed(path, current)
        if keyed_new is not None and keyed_now is not None:
            for key, value in keyed_new.items():
                _diff(keyed_now.get(key), value, _join(path, key), out)
        else:
            for i in range(max(len(current), len(new))):
                _diff(current[i] if i < len(current) else None,
                      new[i] if i < len(new) else None, f"{path}[{i}]", out)
    elif not _same(current, new):
        out[path] = [current, new]
    return out


def renamed_references(value, type_name: str, rename_to: dict, in_layer_list=False):
    """`value` with references to attributes renamed in this call by their new name."""
    if not rename_to:
        return value
    if isinstance(value, list):
        return [renamed_references(v, type_name, rename_to, in_layer_list) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in REFERENCE_FIELDS and isinstance(item, str):
            ref_type = REFERENCE_FIELDS[key][0] or type_name
            out[key] = rename_to.get((ref_type, item), item)
        elif in_layer_list and key == "attributeId" and isinstance(item, str):
            out[key] = rename_to.get(("Layer", item), item)
        else:
            out[key] = renamed_references(item, type_name, rename_to,
                                          type_name == "LayerCombination" and key == LAYER_LIST)
    return out


def _project(full, like):
    """`full` cut down to what `like` names: keys of dicts, keyed list entries."""
    if isinstance(like, dict) and isinstance(full, dict):
        return {k: _project(full.get(k), v) for k, v in like.items()}
    if isinstance(like, list) and isinstance(full, list):
        keyed_like, keyed_full = _keyed("pens", like), _keyed("pens", full)
        if keyed_like is None:
            keyed_like, keyed_full = _keyed(LAYER_LIST, like), _keyed(LAYER_LIST, full)
        if keyed_like is not None and keyed_full is not None:
            return [keyed_full[k] for k in keyed_like if k in keyed_full]
    return full


# ---------- Teamwork ----------

def _teamwork(ctx: _Context, plans: list[Plan], result: dict) -> None:
    from archicad_mcp.core.teamwork import object_set_states, set_problem, teamwork_entries
    live = [p for p in plans if not p.errors and (p.send_fields or p.props
                                                  or p.action == "delete")]
    if not live:
        return
    states = object_set_states(ctx.conn, sorted({p.set_name for p in live}))
    if states is None:
        return
    needed: dict[str, list[str]] = {}
    for p in live:
        needed.setdefault(p.set_name, []).append(f"{p.type_name}/{p.target}")
        problem = set_problem(states[p.set_name])
        if problem is None:
            continue
        advisory = p.advisory_set or (p.action == "modify" and not p.send_fields)
        if problem.blocking and not advisory:
            p.errors.append(problem.text)
        else:
            p.warnings.append(problem.text)
    result["teamwork"] = teamwork_entries(states, needed)


# ---------- the tool ----------

def edit_attributes(conn: ArchicadConnection, changes: list[dict],
                    dry_run: bool = True) -> dict:
    if not changes:
        return field_lists()
    ctx = _Context(conn)
    plans = [_plan(ctx, change, i) for i, change in enumerate(changes)]
    _one_change_per_attribute(plans)
    _check_payloads(ctx, plans)
    result: dict = {"dry_run": dry_run}
    _teamwork(ctx, plans, result)
    # Only renames that will be sent count when the read-back compares names.
    ctx.renamed, ctx.rename_to = {}, {}
    for p in plans:
        if not p.errors and p.action == "modify" and "name" in p.changes:
            old, new = p.changes["name"]
            ctx.renamed[(p.type_name, old)] = ctx.renamed[(p.type_name, new)] = p.guid
            ctx.rename_to[(p.type_name, old)] = new
    result["planned"] = [p.entry() for p in plans if not p.errors]
    skipped = [p.entry() for p in plans if p.errors]
    if skipped:
        result["skipped"] = skipped
    if "teamwork" in result:
        result["teamwork"] = result.pop("teamwork")
    if dry_run:
        return result
    from archicad_mcp.core.attribute_commit import commit
    commit(ctx, [p for p in plans if not p.errors], result)
    return result
