from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
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
    prop_names = sorted({c["property"] for c in changes})
    guids = [c["guid"] for c in changes]
    # Read the raw cells: they carry the property's `type`, which the write
    # payload must echo back (a bare {"value": ...} is rejected by the API).
    cells = fetch_property_cells(conn, guids, prop_names)
    planned = [{"guid": c["guid"], "property": c["property"],
                "current": cells.get(c["guid"], {}).get(c["property"], {}).get("value"),
                "new": c["value"]}
               for c in changes]
    if dry_run:
        return {"dry_run": True, "planned_changes": planned}
    ids = resolve_property_ids(conn, prop_names)
    payload = []
    skipped = []
    for c in changes:
        cell = cells.get(c["guid"], {}).get(c["property"], {})
        value_type = cell.get("type")
        if c["property"] not in ids:
            skipped.append({"guid": c["guid"], "property": c["property"],
                            "reason": "property name did not resolve"})
        elif value_type is None:
            skipped.append({"guid": c["guid"], "property": c["property"],
                            "reason": "could not determine the property's value type "
                                      "(is it available on this element?)"})
        elif value_type in _ENUM_TYPES:
            skipped.append({"guid": c["guid"], "property": c["property"],
                            "reason": f"'{value_type}' properties take an enum value id, "
                                      "not a plain value; set it via execute_write_api_command "
                                      "with the enum's id"})
        else:
            payload.append({
                "elementId": {"guid": c["guid"]},
                "propertyId": ids[c["property"]],
                "propertyValue": {"type": value_type, "status": "normal",
                                  "value": c["value"]}})

    result: dict = {"dry_run": False}
    if payload:
        response = conn.official("API.SetPropertyValuesOfElements",
                                 {"elementPropertyValues": payload})
        execution_results = (response or {}).get("executionResults", [])
        applied = 0
        failed = 0
        for i in range(len(payload)):
            # Lenient: missing/short executionResults (or a missing "success" key)
            # are treated as success rather than crashing.
            success = (execution_results[i].get("success", True)
                       if i < len(execution_results) else True)
            if success:
                applied += 1
            else:
                failed += 1
        result["applied"] = applied
        if failed:
            result["failed"] = failed
    else:
        result["applied"] = 0
    if skipped:
        result["skipped"] = skipped
    return result
