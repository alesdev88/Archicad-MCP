from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    _fetch_classifications,
    _fetch_types,
    element_payload,
    fetch_property_values,
    resolve_property_ids,
)


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
    current = fetch_property_values(conn, guids, prop_names)
    planned = [{"guid": c["guid"], "property": c["property"],
                "current": current.get(c["guid"], {}).get(c["property"]),
                "new": c["value"]}
               for c in changes]
    if dry_run:
        return {"dry_run": True, "planned_changes": planned}
    ids = resolve_property_ids(conn, prop_names)
    payload = []
    skipped = []
    for c in changes:
        if c["property"] in ids:
            payload.append({"elementPropertyValue": {
                "elementId": {"guid": c["guid"]},
                "propertyId": ids[c["property"]],
                "propertyValue": {"value": c["value"]}}})
        else:
            skipped.append({"guid": c["guid"], "property": c["property"]})

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
