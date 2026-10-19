from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core import editability
from archicad_mcp.core.element_address import (
    CLASSIFICATION,
    GDL,
    IFC,
    MARKER,
    PROPERTY,
    Address,
    parse_address,
)
from archicad_mcp.core.element_kinds import (
    find_parameter,
    find_system,
    item_code,
    read_classifications,
    read_ifc,
    read_parameters,
    send_items,
    tapir_requirement,
)
from archicad_mcp.core.element_values import read_value
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    MAX_PROPERTY_FETCH_ELEMENTS,
    PROPERTY_FETCH_CHUNK,
    PropertyFetchTooWideError,
    _fetch_classifications,
    _fetch_types,
    fetch_property_cells,
    fetch_property_values,
    resolve_property_ids,
)

# Enum-valued properties need an EnumValueId, not a plain scalar. Writing them
# is out of scope for this tool; the gateway can do it with an explicit id.
_ENUM_TYPES = frozenset({"singleEnum", "multiEnum"})

# How many readback mismatches a write report lists; the count of the rest is
# enough to act on.
REPORT_CAP = 50

# GUIDs shown per group of identical refusals.
FAILURE_SAMPLE = 5


def cap_list(items: list, limit: int = REPORT_CAP) -> tuple[list, int]:
    """The first `limit` items, and how many were left out."""
    return items[:limit], max(len(items) - limit, 0)


def group_failures(failed: list[dict], detail_key: str = "property") -> list[dict]:
    """Refusals grouped by (code, message), largest group first.

    Each sample names the GUID and `detail_key` of the refused write: the
    property for property writes, the command for modify_elements.

    Refusals come in bulk and alike: 113 doors in hotlinked modules came back
    as 113 identical "TeamWork permission denied" entries in the live run,
    half the characters of the whole task. One count per reason, with a few
    GUIDs to look at, carries the same information.
    """
    groups: dict[tuple, dict] = {}
    for f in failed:
        group = groups.setdefault((f["code"], f["message"]), {
            "code": f["code"], "message": f["message"], "count": 0, "sample": []})
        group["count"] += 1
        if len(group["sample"]) < FAILURE_SAMPLE:
            group["sample"].append({"guid": f["guid"], detail_key: f[detail_key]})
    return sorted(groups.values(), key=lambda g: -g["count"])


# Property value types that take a plain number. The measure types are numbers
# in the API; the unit is the project's, not part of the value.
_NUMERIC_TYPES = frozenset({"number", "length", "area", "volume", "angle"})


def value_fit(value_type: str, value) -> tuple[object, str | None]:
    """The value to send for a property of `value_type`, and why it does not fit.

    Checked before anything is written, because Archicad's refusal arrives per
    element after the whole batch is sent, and one type mistake then fails every
    element the same way. The case that forced this: a 3-digit door number
    "001" aimed at an Integer property, which cannot hold a leading zero.
    """
    if value_type == "string":
        if isinstance(value, str):
            return value, None
        return value, f"'string' property takes text, got {value!r}"
    if value_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            try:
                number = int(value)
            except ValueError:
                number = None
            if number is not None and str(number) == value:
                return number, None
        return value, (f"'integer' property cannot hold {value!r}; send a whole "
                       "number, or change the property to String in Property "
                       "Manager if the value needs leading zeros")
    if value_type in _NUMERIC_TYPES:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), None
        return value, f"'{value_type}' property takes a number, got {value!r}"
    if value_type == "boolean":
        if isinstance(value, bool):
            return value, None
        return value, f"'boolean' property takes true or false, got {value!r}"
    # Types this check does not know are left to Archicad, whose refusal is
    # still reported per element by send_property_writes.
    return value, None


def plan_property_writes(conn: ArchicadConnection,
                         changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Check each change against the element's current cell and build its payload.

    Returns (planned, skipped). Planned items carry the finished
    SetPropertyValuesOfElements entry, so sending needs no further reads.
    Nothing is written here.
    """
    prop_names = sorted({c["property"] for c in changes})
    guids = list(dict.fromkeys(c["guid"] for c in changes))
    # Read the raw cells: they carry the property's `type`, which the write
    # payload must echo back (a bare {"value": ...} is rejected by the API).
    cells = fetch_property_cells(conn, guids, prop_names)
    ids = resolve_property_ids(conn, prop_names)
    # Archicad would refuse these one by one after the batch is sent;
    # core.editability finds them first (hotlinks, reservations, active window).
    refusals, _ = editability.refusals(conn, guids)
    planned: list[dict] = []
    skipped: list[dict] = []
    for c in changes:
        guid, prop = c["guid"], c["property"]
        cell = cells.get(guid, {}).get(prop, {})
        value_type = cell.get("type")
        send = c["value"]
        if prop not in ids:
            reason = "property name did not resolve"
        elif guid in refusals:
            reason = refusals[guid]
        elif value_type is None:
            reason = ("could not determine the property's value type "
                      "(is it available on this element?)")
        elif value_type in _ENUM_TYPES:
            reason = (f"'{value_type}' properties take an enum value id, not a "
                      "plain value; set it via execute_write_api_command (or "
                      "ac.cmd in a script) with the enum's id")
        else:
            send, reason = value_fit(value_type, c["value"])
        if reason is not None:
            skipped.append({"guid": guid, "property": prop, "reason": reason})
            continue
        planned.append({
            "guid": guid, "property": prop, "current": cell.get("value"), "new": send,
            "payload": {"elementId": {"guid": guid}, "propertyId": ids[prop],
                        "propertyValue": {"type": value_type, "status": "normal",
                                          "value": send}}})
    return planned, skipped


def send_property_writes(
        conn: ArchicadConnection, planned: list[dict],
) -> tuple[int, list[dict], APIErrorBase | ArchicadUnavailableError | None]:
    """Send planned writes in batches.

    Returns (applied, failed per element, the error that stopped sending). A
    batch Archicad refuses as a whole stops the sending, but the batches before
    it have already landed, so their count and refusals come back with the
    error rather than being lost to an exception. The error is None when every
    batch was sent; applied plus the failed count is then how many were sent.
    """
    sent = send_items(conn.official, "API.SetPropertyValuesOfElements",
                      "elementPropertyValues", [p["payload"] for p in planned],
                      PROPERTY_FETCH_CHUNK)
    applied = 0
    failed: list[dict] = []
    for p, outcome in zip(planned, sent.outcomes):
        if outcome == "ok":
            applied += 1
        elif isinstance(outcome, dict):
            # Each failure names its element and Archicad's reason (6001 is a
            # Teamwork reservation, including elements inside a hotlink), so
            # the caller can act on it without a second query pass. Callers
            # group them with group_failures before reporting.
            failed.append({"guid": p["guid"], "property": p["property"], **outcome})
    return applied, failed, sent.stopped


def error_fields(exc: APIErrorBase | ArchicadUnavailableError) -> dict:
    """{"code", "message"} of an error that stopped a write run."""
    if isinstance(exc, APIErrorBase):
        return {"code": getattr(exc, "code", None), "message": exc.message}
    return {"code": None, "message": str(exc)}


# A wildcard reads every parameter of every element: hundreds of values per
# door. It is for finding names on a few elements.
WILDCARD_ELEMENT_LIMIT = 20

_PREFIX = {CLASSIFICATION: "classification:", GDL: "gdl:", MARKER: "marker:", IFC: "ifc:"}
_READ_COMMANDS = {GDL: "GetGDLParametersOfElements", MARKER: "GetMarkerParametersOfElements",
                  IFC: "GetIFCPropertiesOfElements"}


def check_width(guids: list[str]) -> None:
    """Refuse a read across more elements than the property ceiling allows."""
    if len(guids) > MAX_PROPERTY_FETCH_ELEMENTS:
        raise PropertyFetchTooWideError(
            f"Refusing to read element data across {len(guids)} elements (limit "
            f"{MAX_PROPERTY_FETCH_ELEMENTS}). Scope the query first, or raise "
            "ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS if you accept the risk.")


def parse_addresses(texts: list[str]) -> list[Address] | str:
    out = []
    for text in texts:
        address = parse_address(text)
        if isinstance(address, str):
            return address
        out.append(address)
    return out


def _parameter_values(item: dict, address: Address, reads: dict, requirement: str | None,
                      unreadable: dict, labels: dict) -> None:
    """Put a gdl:, marker: or ifc: address's values into item["properties"]."""
    kind, guid, out = address.kind, item["guid"], item["properties"]
    entry = None if requirement else reads.get(guid, {})
    if requirement:
        problem = requirement
    elif kind == IFC:
        problem = entry if isinstance(entry, str) else None
    else:
        problem = entry.get("error")
    if problem:
        unreadable[kind] = problem
        if not address.wildcard:
            out[address.text] = None
        return
    if kind == IFC:
        if address.wildcard:
            out.update({f"{_PREFIX[IFC]}{k}": v for k, v in entry.items()})
        else:
            out[address.text] = entry.get(address.key)
        return
    params = entry["params"]
    if not address.wildcard:
        param = find_parameter(params, address.key)
        out[address.text] = read_value(param) if param else None
        return
    for name, param in params.items():
        if "Hidden" in (param.get("flags") or []):
            continue
        key = f"{_PREFIX[kind]}{name}"
        out[key] = read_value(param)
        label = param.get("displayName")
        if label and label != name:
            labels[key] = label


def get_element_data(conn: ArchicadConnection, guids: list[str],
                     properties: list[str] | None = None,
                     include_classifications: bool = False) -> dict:
    addresses = parse_addresses(properties or [])
    if isinstance(addresses, str):
        return {"error": addresses}
    if any(a.wildcard for a in addresses) and len(guids) > WILDCARD_ELEMENT_LIMIT:
        return {"error": (f"a wildcard address reads every value of every element, so it "
                          f"takes at most {WILDCARD_ELEMENT_LIMIT} elements per call "
                          f"({len(guids)} given). Read one element with the wildcard, "
                          "then name what you need for the rest.")}
    kinds = {a.kind for a in addresses}
    if kinds - {PROPERTY}:
        check_width(guids)
    names = [a.key for a in addresses if a.kind == PROPERTY]
    types = _fetch_types(conn, guids)
    values = fetch_property_values(conn, guids, [BUILTIN_LAYER, *names])
    classif = _fetch_classifications(conn, guids) if include_classifications else {}

    systems: dict[str, str] = {}
    index = None
    if CLASSIFICATION in kinds:
        from archicad_mcp.core.definition_edit import ClassificationIndex
        index = ClassificationIndex.load(conn)
        for a in addresses:
            if a.kind != CLASSIFICATION:
                continue
            found = list(index.systems.values()) if a.wildcard else [find_system(index, a.key)]
            for system in found:
                if isinstance(system, str):
                    return {"error": system}
                systems[system.guid] = system.label
    classes = read_classifications(conn, guids, systems) if systems else {}
    requirements = {k: tapir_requirement(conn, _READ_COMMANDS[k])
                    for k in kinds & set(_READ_COMMANDS)}
    reads: dict[str, dict] = {}
    for kind in (GDL, MARKER):
        if kind in requirements and requirements[kind] is None:
            reads[kind] = read_parameters(conn, guids, kind)
    if IFC in requirements and requirements[IFC] is None:
        reads[IFC] = read_ifc(conn, guids)

    elements = []
    for g in guids:
        item = {"guid": g, "type": types.get(g, ""),
                "layer": values.get(g, {}).get(BUILTIN_LAYER), "properties": {}}
        labels: dict[str, str] = {}
        unreadable: dict[str, str] = {}
        for a in addresses:
            if a.kind == PROPERTY:
                item["properties"][a.text] = values.get(g, {}).get(a.key)
            elif a.kind == CLASSIFICATION:
                read = classes.get(g, {})
                if a.wildcard:
                    item["properties"].update(
                        {f"{_PREFIX[CLASSIFICATION]}{label}": item_code(index, read.get(label))
                         for label in systems.values()})
                else:
                    label = find_system(index, a.key).label
                    item["properties"][a.text] = item_code(index, read.get(label))
            else:
                _parameter_values(item, a, reads.get(a.kind, {}), requirements[a.kind],
                                  unreadable, labels)
        if include_classifications:
            item["classifications"] = classif.get(g, {})
        if labels:
            item["labels"] = labels
        if unreadable:
            item["unreadable"] = unreadable
        elements.append(item)
    return {"elements": elements}


def set_element_data(conn: ArchicadConnection, changes: list[dict],
                     dry_run: bool = True) -> dict:
    if dry_run:
        prop_names = sorted({c["property"] for c in changes})
        cells = fetch_property_cells(conn, [c["guid"] for c in changes], prop_names)
        return {"dry_run": True, "planned_changes": [
            {"guid": c["guid"], "property": c["property"],
             "current": cells.get(c["guid"], {}).get(c["property"], {}).get("value"),
             "new": c["value"]}
            for c in changes]}
    planned, skipped = plan_property_writes(conn, changes)
    result: dict = {"dry_run": False, "applied": 0}
    if planned:
        applied, failed, error = send_property_writes(conn, planned)
        result["applied"] = applied
        if failed:
            result["failed"] = group_failures(failed)
        if error is not None:
            # Earlier batches landed; an {"error"} alone would hide them.
            result["stopped"] = error_fields(error)
    if skipped:
        result["skipped"] = skipped
    return result
