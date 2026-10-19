"""set_element_data for every kind of element data, dry run by default.

Planning reads every value it will change, once per kind, and runs the
editability check once per call, so the dry run shows current -> new for
exactly what the commit would send and names every change Archicad would
refuse before anything is sent. The commit sends classifications first
(property availability follows classification), then properties, GDL and
marker parameters, IFC properties, and reads back every change: a Parameter
Script can replace a written GDL value while Tapir answers success. The design
is docs/superpowers/specs/2026-10-19-element-data-kinds-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import editability
from archicad_mcp.core import element_values as ev
from archicad_mcp.core.attributes import tapir_attributes
from archicad_mcp.core.element_address import (
    CLASSIFICATION,
    GDL,
    IFC,
    MARKER,
    PROPERTY,
    Address,
    ifc_parts,
    parse_address,
)
from archicad_mcp.core.element_kinds import (
    PARAMETER_COMMANDS,
    find_parameter,
    find_system,
    item_code,
    read_classifications,
    read_ifc,
    read_parameters,
    tapir_requirement,
    tapir_version,
)
from archicad_mcp.extract import _fetch_types

# The types whose GDL parameters Tapir writes back with the element: its
# SetGDLParametersOfElements patches the size of these and no others.
GDL_WRITE_TYPES = ("Object", "Lamp", "Door", "Window", "Skylight")
_SIZE_TYPES = ("Object", "Lamp")
# Tapir before this wrote A and B of an object whose size is not fixed in the
# stored unit (size / the part's own A or B), so the object came out A x libA.
# Fixed upstream in #718, first released in 1.6.0.
SIZE_FIX_TAPIR = (1, 6, 0)
# From Archicad 28 Tapir answers every SetIFCPropertiesOfElements with "not
# supported": the IFCAPI that replaced ACAPI_Element_SetIFCProperty has no
# write there yet (IFCCommands.cpp, 1.7.0 and 1.7.1).
IFC_WRITE_BEFORE = 28
IFC_UNSUPPORTED = ("Tapir cannot write IFC properties in Archicad 28 and newer (it answers "
                   "'Writing IFC properties is not supported in Archicad 28 and newer "
                   "yet'). Map them from Archicad properties in the IFC translator, or set "
                   "them in the IFC Manager")
UNAVAILABLE = "could not determine the property's value type"
SECOND_CALL = (" It may become available with this call's classification change: send "
               "the classification first, then this property in a second call.")

_STRING_LIMIT = {GDL: ev.GDL_STRING_LIMIT, MARKER: ev.MARKER_STRING_LIMIT}


@dataclass
class Change:
    order: int
    guid: str
    address: Address
    value: object
    ifc_type: str | None = None
    current: object = None
    new: object = None
    send: object = None        # as Tapir or Archicad receives it
    target: object = None      # what the readback must find
    warning: str | None = None
    name: str | None = None    # a GDL or marker parameter as the part spells it
    payload: dict | None = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    skipped: list[tuple[int, dict]] = field(default_factory=list)
    index: object = None                                     # ClassificationIndex
    systems: dict[str, str] = field(default_factory=dict)    # system GUID -> label

    def skip(self, change: Change, reason: str) -> None:
        self.skipped.append((change.order, {"guid": change.guid,
                                            "property": change.address.text,
                                            "reason": reason}))


class _Skip(Exception):
    """One change cannot be sent; the message is the reason."""


def _identity(c: Change) -> tuple:
    key = c.address.key.casefold() if c.address.kind in (GDL, MARKER) else c.address.key
    return c.guid.upper(), c.address.kind, key


def _parse(changes, plan: Plan) -> list[Change]:
    """The well-formed changes; the rest are skipped with the reason."""
    parsed: list[Change] = []
    for order, raw in enumerate(changes if isinstance(changes, list) else []):
        if not isinstance(raw, dict):
            plan.skipped.append((order, {"guid": None, "property": None, "reason": (
                "a change is an object {guid, property, value}")}))
            continue
        guid, text = raw.get("guid"), raw.get("property")
        address = parse_address(text) if isinstance(text, str) else None
        change = Change(order, guid if isinstance(guid, str) else None,
                        address or Address(str(text), PROPERTY, str(text), False),
                        raw.get("value"), raw.get("ifc_type"))
        if not isinstance(guid, str) or not guid.strip():
            plan.skip(change, "the change has no guid")
        elif not isinstance(text, str):
            plan.skip(change, "the change has no property address")
        elif isinstance(address, str):
            change.address = Address(text, PROPERTY, text, False)
            plan.skip(change, address)
        elif "value" not in raw:
            plan.skip(change, "the change has no value")
        elif address.wildcard:
            plan.skip(change, "a wildcard reads many values; a change names one")
        elif change.ifc_type is not None and address.kind != IFC:
            plan.skip(change, "ifc_type goes with an ifc: address only")
        elif change.value is None and address.kind not in (PROPERTY, CLASSIFICATION):
            plan.skip(change, "a value is required; null only unclassifies "
                              "(classification:<System>)")
        else:
            parsed.append(change)
    groups: dict[tuple, list[Change]] = {}
    for c in parsed:
        groups.setdefault(_identity(c), []).append(c)
    kept = []
    for same in groups.values():
        values = {json.dumps([c.value, c.ifc_type], sort_keys=True, default=str) for c in same}
        if len(values) > 1:
            for c in same:
                plan.skip(c, "given two different values")
        else:
            kept.append(same[0])
    return sorted(kept, key=lambda c: c.order)


def _attribute_resolver(conn: ArchicadConnection):
    """resolve(attribute_type, name) -> index, and name_of(attribute_type, index)."""
    tables: dict[str, list[dict]] = {}

    def table(kind: str) -> list[dict]:
        if kind not in tables:
            tables[kind] = tapir_attributes(conn, kind)
        return tables[kind]

    def resolve(kind: str, name: str) -> int:
        exact = [a for a in table(kind) if a["name"] == name]
        loose = exact or [a for a in table(kind)
                          if a["name"].strip().casefold() == name.strip().casefold()]
        if len(loose) == 1:
            return loose[0]["index"]
        raise ValueError(f"more than one {kind} is named '{name}'" if loose
                         else f"no {kind} named '{name}'")

    def name_of(kind: str, index):
        return next((a["name"] for a in table(kind) if a["index"] == index), index)

    return resolve, name_of


# ---------- per kind ----------

def _plan_properties(conn: ArchicadConnection, changes: list[Change], plan: Plan,
                     classified: set[str]) -> None:
    from archicad_mcp.core.element_data import plan_property_writes
    planned, skipped = plan_property_writes(
        conn, [{"guid": c.guid, "property": c.address.key, "value": c.value}
               for c in changes], refusals={})
    by_key = {(c.guid, c.address.key): c for c in changes}
    for p in planned:
        c = by_key[(p["guid"], p["property"])]
        c.current, c.new, c.target, c.payload = p["current"], p["new"], p["new"], p["payload"]
        plan.changes.append(c)
    for s in skipped:
        c = by_key[(s["guid"], s["property"])]
        reason = s["reason"]
        if reason.startswith(UNAVAILABLE) and c.guid.upper() in classified:
            reason += SECOND_CALL
        plan.skip(c, reason)


def _resolve_item(index, system, value) -> str | None:
    """The item GUID a classification value names within `system`; None unclassifies."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Skip("takes an item ID, an item GUID, or null to unclassify")
    by_code = [g for g, i in index.items.items() if i.system == system.label and i.code == value]
    if len(by_code) > 1:
        raise _Skip(f"'{value}' is the ID of {len(by_code)} items in {system.label}; "
                    "address it by GUID")
    if by_code:
        return by_code[0]
    item = next((i for g, i in index.items.items() if g.upper() == value.upper()), None)
    if item is not None:
        if item.system != system.label:
            raise _Skip(f"item {value} belongs to {item.system}, not {system.label}")
        return item.guid
    raise _Skip(f"no item '{value}' in {system.label} (give its ID or GUID)")


def _plan_classifications(conn: ArchicadConnection, changes: list[Change], plan: Plan) -> None:
    from archicad_mcp.core.definition_edit import ClassificationIndex
    plan.index = index = ClassificationIndex.load(conn)
    ready = []
    for c in changes:
        system = find_system(index, c.address.key)
        try:
            if isinstance(system, str):
                raise _Skip(system)
            c.target = _resolve_item(index, system, c.value)
        except _Skip as skip:
            plan.skip(c, str(skip))
            continue
        c.name = system.label
        plan.systems[system.guid] = system.label
        ready.append((c, system))
    if not ready:
        return
    current = read_classifications(conn, list(dict.fromkeys(c.guid for c, _ in ready)),
                                   plan.systems)
    for c, system in ready:
        c.current = item_code(index, current.get(c.guid, {}).get(system.label))
        c.new = item_code(index, c.target)
        identifier = {"classificationSystemId": {"guid": system.guid}}
        if c.target is not None:
            identifier["classificationItemId"] = {"guid": c.target}
        c.payload = {"elementId": {"guid": c.guid}, "classificationId": identifier}
        plan.changes.append(c)


def _plan_parameters(conn: ArchicadConnection, kind: str, changes: list[Change],
                     plan: Plan) -> None:
    command, _, write = PARAMETER_COMMANDS[kind][:3]
    missing = tapir_requirement(conn, command) or tapir_requirement(conn, write)
    if missing:
        for c in changes:
            plan.skip(c, missing)
        return
    guids = list(dict.fromkeys(c.guid for c in changes))
    types = _fetch_types(conn, guids) if kind == GDL else {}
    old_tapir = kind == GDL and (tapir_version(conn) or (0, 0, 0)) < SIZE_FIX_TAPIR
    reads = read_parameters(conn, guids, kind)
    resolve, name_of = _attribute_resolver(conn)
    for c in changes:
        try:
            element_type = types.get(c.guid)
            if kind == GDL and element_type not in GDL_WRITE_TYPES:
                raise _Skip(f"GDL parameters are written on {', '.join(GDL_WRITE_TYPES)}; "
                            f"this is {'a ' + element_type if element_type else 'not found'}")
            read = reads.get(c.guid, {})
            if "error" in read:
                raise _Skip(read["error"])
            entry = find_parameter(read["params"], c.address.key)
            if entry is None:
                raise _Skip(f"no {kind} parameter '{c.address.key}' on this element; "
                            f"read {kind}:* with get_element_data to list them")
            if old_tapir and element_type in _SIZE_TYPES and entry["name"] in ("A", "B"):
                raise _Skip(f"Tapir before {'.'.join(map(str, SIZE_FIX_TAPIR))} writes an "
                            "object's A and B in the wrong unit when its size is not fixed; "
                            "update Tapir (install_tapir), or change the size with "
                            "modify_elements")
            value, reason = ev.fit(entry, c.value, string_limit=_STRING_LIMIT[kind],
                                   arrays=kind == GDL, resolve_attribute=resolve)
            if reason is not None:
                raise _Skip(reason)
        except _Skip as skip:
            plan.skip(c, str(skip))
            continue
        c.name, c.target, c.send = entry["name"], value, ev.to_tapir(entry, value, kind)
        c.current, c.new = ev.read_value(entry), value
        attribute = ev.ATTRIBUTE_TYPES.get(entry.get("type"))
        if attribute and isinstance(c.value, str):
            c.current, c.new = name_of(attribute, c.current), c.value
        c.warning = "; ".join(ev.warnings(entry, value)) or None
        plan.changes.append(c)


def _plan_ifc(conn: ArchicadConnection, changes: list[Change], plan: Plan) -> None:
    version = conn.archicad_version()
    missing = (IFC_UNSUPPORTED if version is not None and version >= IFC_WRITE_BEFORE
               else tapir_requirement(conn, "SetIFCPropertiesOfElements")
               or tapir_requirement(conn, "GetIFCPropertiesOfElements"))
    if missing:
        for c in changes:
            plan.skip(c, missing)
        return
    reads = read_ifc(conn, list(dict.fromkeys(c.guid for c in changes)))
    for c in changes:
        c.ifc_type = c.ifc_type or ev.ifc_type_for(c.value)
        read = reads.get(c.guid, {})
        if isinstance(read, str):
            plan.skip(c, read)
            continue
        if not isinstance(c.ifc_type, str) or not c.ifc_type:
            plan.skip(c, f"an IFC property takes text, a number or true/false, got {c.value!r}")
            continue
        reason = ev.ifc_fit(c.value, c.ifc_type)
        if reason is not None:
            plan.skip(c, reason)
            continue
        pset, name = ifc_parts(c.address)
        c.current, c.new, c.target, c.send = read.get(c.address.key), c.value, c.value, c.value
        c.payload = {"elementId": {"guid": c.guid}, "propertySetName": pset,
                     "propertyName": name, "type": c.ifc_type, "value": c.value}
        plan.changes.append(c)


def plan(conn: ArchicadConnection, changes) -> Plan:
    """Read, resolve and check every change; nothing is written."""
    result = Plan()
    parsed = _parse(changes, result)
    guids = list({c.guid.upper(): c.guid for c in parsed}.values())
    refusals, _ = editability.refusals(conn, guids) if guids else ({}, None)
    refused = {g.upper(): reason for g, reason in refusals.items()}
    by_kind: dict[str, list[Change]] = {}
    for c in parsed:
        if c.guid.upper() in refused:
            result.skip(c, refused[c.guid.upper()])
        else:
            by_kind.setdefault(c.address.kind, []).append(c)
    if CLASSIFICATION in by_kind:
        _plan_classifications(conn, by_kind[CLASSIFICATION], result)
    if PROPERTY in by_kind:
        classified = {c.guid.upper() for c in result.changes
                      if c.address.kind == CLASSIFICATION}
        _plan_properties(conn, by_kind[PROPERTY], result, classified)
    for kind in (GDL, MARKER):
        if kind in by_kind:
            _plan_parameters(conn, kind, by_kind[kind], result)
    if IFC in by_kind:
        _plan_ifc(conn, by_kind[IFC], result)
    result.changes.sort(key=lambda c: c.order)
    result.skipped.sort(key=lambda item: item[0])
    return result


def planned_entry(c: Change) -> dict:
    entry = {"guid": c.guid, "property": c.address.text, "current": c.current, "new": c.new}
    if c.address.kind == IFC:
        entry["ifc_type"] = c.ifc_type
    if c.warning:
        entry["warning"] = c.warning
    return entry


def skipped_entries(p: Plan) -> list[dict]:
    return [entry for _, entry in p.skipped]


def dry_run_result(p: Plan) -> dict:
    result = {"dry_run": True, "planned_changes": [planned_entry(c) for c in p.changes]}
    if p.skipped:
        result["skipped"] = skipped_entries(p)
    return result
