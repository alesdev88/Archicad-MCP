"""Reading and sending element data beyond property values: classifications,
GDL parameters, door and window marker parameters, local IFC properties.

Every reader matches Tapir's answer to the request by position: Tapir answers
GUIDs uppercase whatever case they were sent in, and error entries carry no
GUID at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.extract import (
    PROPERTY_FETCH_CHUNK,
    element_payload,
    parse_ifc_response,
)

# kind -> (read command, its list key, write command, its list key, the
# per-element parameter key)
PARAMETER_COMMANDS = {
    "gdl": ("GetGDLParametersOfElements", "gdlParametersOfElements",
            "SetGDLParametersOfElements", "elementsWithGDLParameters", "gdlParameters"),
    "marker": ("GetMarkerParametersOfElements", "markerParametersOfElements",
               "SetMarkerParametersOfElements", "elementsWithMarkerParameters",
               "markerParameters"),
}

# Each answer carries every parameter of the part with its value list: a door
# is hundreds of entries.
PARAMETER_CHUNK = 50

_ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_ERRORS = (APIErrorBase, ArchicadUnavailableError)


def tapir_version(conn: ArchicadConnection) -> tuple[int, int, int] | None:
    """The Tapir add-on's reported version, or None without Tapir."""
    if not conn.tapir_available():
        return None
    raw = str(conn.tapir("GetAddOnVersion").get("version", ""))
    numbers = [int(n) for n in re.findall(r"\d+", raw)[:3]]
    return tuple((numbers + [0, 0, 0])[:3])


def tapir_requirement(conn: ArchicadConnection, command: str) -> str | None:
    """None when the running Tapir has the command, else what to install."""
    if conn.tapir_command_available(command):
        return None
    from archicad_mcp.gateway.registry import build_registry
    from archicad_mcp.tapir_install.hint import install_hint
    since = build_registry()[command].version
    return (f"needs Tapir {since} or newer ({command}). "
            + install_hint(conn.archicad_version(), replace=conn.tapir_available()))


def _error_message(item) -> str:
    error = item.get("error", {}) if isinstance(item, dict) else {}
    return error.get("message") or "Archicad did not read it"


def read_parameters(conn: ArchicadConnection, guids: list[str],
                    kind: str) -> dict[str, dict]:
    """guid -> {"params": {name: Tapir's entry}} or {"error": message}."""
    command, list_key = PARAMETER_COMMANDS[kind][:2]
    out: dict[str, dict] = {}
    for start in range(0, len(guids), PARAMETER_CHUNK):
        chunk = guids[start:start + PARAMETER_CHUNK]
        items = conn.tapir(command, {"elements": element_payload(chunk)}).get(list_key, [])
        for i, guid in enumerate(chunk):
            item = items[i] if i < len(items) else None
            if not isinstance(item, dict) or "error" in item or "parameters" not in item:
                out[guid] = {"error": _error_message(item)}
                continue
            out[guid] = {"params": {p["name"]: p for p in item["parameters"]
                                    if isinstance(p, dict) and "name" in p}}
    return out


def find_parameter(params: dict[str, dict], name: str) -> dict | None:
    """The parameter called `name`: exact, else the only case-insensitive match
    (GDL names are case-insensitive)."""
    if name in params:
        return params[name]
    loose = [p for n, p in params.items() if n.casefold() == name.casefold()]
    return loose[0] if len(loose) == 1 else None


def read_classifications(conn: ArchicadConnection, guids: list[str],
                         systems: dict[str, str]) -> dict[str, dict[str, str | None]]:
    """guid -> system label -> item GUID, None when unclassified.

    `systems` maps system GUID -> label. Tapir's read reaches sub-elements of
    curtain walls, stairs and railings; the official one is the fallback. The
    official answer nests system and item under classificationId, the Tapir
    1.7.1 source answers them directly; both are read.
    """
    tapir = conn.tapir_command_available("GetClassificationsOfElements")
    call = conn.tapir if tapir else conn.official
    command = "GetClassificationsOfElements" if tapir else "API.GetClassificationsOfElements"
    labels = {g.upper(): label for g, label in systems.items()}
    system_payload = [{"classificationSystemId": {"guid": g}} for g in systems]
    out: dict[str, dict[str, str | None]] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        rows = call(command, {"elements": element_payload(chunk),
                              "classificationSystemIds": system_payload}
                    ).get("elementClassifications", [])
        for i, guid in enumerate(chunk):
            per_system = dict.fromkeys(systems.values())
            row = rows[i] if i < len(rows) else {}
            for item in (row or {}).get("classificationIds", []) if isinstance(row, dict) else []:
                cid = item.get("classificationId", item) if isinstance(item, dict) else {}
                label = labels.get(str(cid.get("classificationSystemId", {}).get("guid", "")).upper())
                inner = (cid.get("classificationItemId") or {}).get("guid")
                if label is not None and inner and inner != _ZERO_GUID:
                    per_system[label] = inner
            out[guid] = per_system
    return out


def find_system(index, label: str):
    """The classification system an address names (a ClassificationIndex
    system), or why there is none. `label` is the name, or "name version"
    when two systems share a name."""
    if label in index.systems:
        return index.systems[label]
    if label in index.ambiguous:
        return (f"'{label}' names {len(index.ambiguous[label])} classification systems "
                f"({', '.join(index.ambiguous[label])}); write the version too")
    loose = [s for name, s in index.systems.items() if name.casefold() == label.casefold()]
    if len(loose) == 1:
        return loose[0]
    known = ", ".join(sorted(index.systems)) or "none"
    return f"no classification system '{label}'; the systems here: {known}"


def item_code(index, guid: str | None) -> str | None:
    """An item GUID as its ID (code); the GUID itself when the index lacks it."""
    if guid is None:
        return None
    item = index.items.get(guid) or next(
        (i for g, i in index.items.items() if g.upper() == guid.upper()), None)
    return item.code if item else guid


def read_ifc(conn: ArchicadConnection, guids: list[str]) -> dict[str, dict[str, str] | str]:
    """guid -> {"Pset.Name": text}, or the error message for the element."""
    out: dict[str, dict[str, str] | str] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        response = conn.tapir("GetIFCPropertiesOfElements", {"elements": element_payload(chunk)})
        out.update(parse_ifc_response(chunk, response))
    return out


@dataclass
class Sent:
    """Per item: "ok", {"code", "message"} for a refusal, "unanswered" in the
    batch that raised (Archicad may have acted before the error), "not_sent"
    after it."""
    outcomes: list
    stopped: APIErrorBase | ArchicadUnavailableError | None = None


def send_items(call, command: str, list_key: str, items: list, chunk: int) -> Sent:
    """Send `items` in batches; one execution result per item.

    A batch that raises stops the sending, and the batches before it have
    landed, so their outcomes come back with the error rather than being lost
    to an exception. A missing result counts as success, as property writes
    always have.
    """
    outcomes: list = []
    for start in range(0, len(items), chunk):
        batch = items[start:start + chunk]
        try:
            response = call(command, {list_key: batch})
        except _ERRORS as exc:
            outcomes.extend(["unanswered"] * len(batch))
            outcomes.extend(["not_sent"] * (len(items) - start - len(batch)))
            return Sent(outcomes, exc)
        results = (response or {}).get("executionResults", [])
        for i in range(len(batch)):
            outcome = results[i] if i < len(results) else {}
            if not isinstance(outcome, dict) or outcome.get("success", True):
                outcomes.append("ok")
                continue
            error = outcome.get("error") or {}
            outcomes.append({"code": error.get("code"), "message": error.get("message")})
    return Sent(outcomes)
