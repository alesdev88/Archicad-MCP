from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    PROPERTY_FETCH_CHUNK,
    _fetch_classifications,
    _fetch_types,
    element_payload,
    fetch_property_cells,
    fetch_property_values,
    resolve_property_ids,
)

# Enum-valued properties need an EnumValueId, not a plain scalar. Writing them
# is out of scope for this tool; the gateway can do it with an explicit id.
_ENUM_TYPES = frozenset({"singleEnum", "multiEnum"})

# How many per-element failures a write report lists. A set of thousands of
# unreserved Teamwork elements would otherwise return thousands of identical
# refusals; the count of the rest is enough to act on.
REPORT_CAP = 50


def cap_list(items: list, limit: int = REPORT_CAP) -> tuple[list, int]:
    """The first `limit` items, and how many were left out."""
    return items[:limit], max(len(items) - limit, 0)


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
    planned: list[dict] = []
    skipped: list[dict] = []
    for c in changes:
        guid, prop = c["guid"], c["property"]
        cell = cells.get(guid, {}).get(prop, {})
        value_type = cell.get("type")
        send = c["value"]
        if prop not in ids:
            reason = "property name did not resolve"
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
    applied = 0
    failed: list[dict] = []
    for start in range(0, len(planned), PROPERTY_FETCH_CHUNK):
        chunk = planned[start:start + PROPERTY_FETCH_CHUNK]
        try:
            response = conn.official(
                "API.SetPropertyValuesOfElements",
                {"elementPropertyValues": [p["payload"] for p in chunk]})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            return applied, failed, exc
        results = (response or {}).get("executionResults", [])
        for i, p in enumerate(chunk):
            # Lenient: missing/short executionResults (or a missing "success" key)
            # are treated as success rather than crashing.
            outcome = results[i] if i < len(results) else {}
            if outcome.get("success", True):
                applied += 1
                continue
            # Each failure names its element and Archicad's reason (6001 is a
            # Teamwork reservation, including elements inside a hotlink), so
            # the caller can act on it without a second query pass.
            error = outcome.get("error") or {}
            failed.append({"guid": p["guid"], "property": p["property"],
                           "code": error.get("code"), "message": error.get("message")})
    return applied, failed, None


def error_fields(exc: APIErrorBase | ArchicadUnavailableError) -> dict:
    """{"code", "message"} of an error that stopped a write run."""
    if isinstance(exc, APIErrorBase):
        return {"code": getattr(exc, "code", None), "message": exc.message}
    return {"code": None, "message": str(exc)}


def get_element_data(conn: ArchicadConnection, guids: list[str],
                     properties: list[str] | None = None,
                     include_classifications: bool = False) -> dict:
    properties = properties or []
    types = _fetch_types(conn, guids)
    values = fetch_property_values(conn, guids, [BUILTIN_LAYER, *properties])
    classif = _fetch_classifications(conn, guids) if include_classifications else {}
    elements = []
    for g in guids:
        item = {"guid": g, "type": types.get(g, ""),
                "layer": values.get(g, {}).get(BUILTIN_LAYER),
                "properties": {p: values.get(g, {}).get(p) for p in properties}}
        if include_classifications:
            item["classifications"] = classif.get(g, {})
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
            result["failed"], not_shown = cap_list(failed)
            if not_shown:
                result["failed_not_shown"] = not_shown
        if error is not None:
            # Earlier batches landed; an {"error"} alone would hide them.
            result["stopped"] = error_fields(error)
    if skipped:
        result["skipped"] = skipped
    return result
