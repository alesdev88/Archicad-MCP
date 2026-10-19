"""Send an apply_favorites plan and report what the favourite changed.

Order: a details read, one ApplyFavoritesToElements call, the writes that put
back what `keep` names, then a second read. Success per element only says
Archicad took the call, so `changed` comes from the two reads: every field
whose value differs, nested details flattened to dotted names, the layer and
attribute references shown by name. Classifications, categories and property
values are not read back: property reads are the class of read that has
crashed Archicad 29.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.attributes import tapir_attributes
from archicad_mcp.core.element_data import cap_list, error_fields, group_failures
from archicad_mcp.core.favorites import FLAGS, FavoritePlan, _favorite_fields, _notes, _read
from archicad_mcp.core.modify_fields import ATTRIBUTE_FIELDS
from archicad_mcp.core.mutate import _group
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK, resolve_property_ids

DETAIL_FIELDS = ["type", "id", "layerIndex", "drawIndex", "details"]
ELEMENT_ID = "General_ElementID"
_ERRORS = (APIErrorBase, ArchicadUnavailableError)
# Library part identity GUIDs change with the part; its name says the same.
_LEFT_OUT = frozenset({"ownUnID", "parentUnID"})
_MESSAGES = {
    -2130313113: "Archicad has no favourite of that name",
    -2130312311: ("Archicad refused the favourite's settings for this element (Tapir "
                  "issue #812, reported for windows and doors on some Archicad versions)"),
}


def _is_ref(value) -> bool:
    return isinstance(value, dict) and set(value) == {"guid"}


def _flatten(value, prefix: str, out: dict) -> None:
    if isinstance(value, dict) and not _is_ref(value):
        for key, inner in value.items():
            if key not in _LEFT_OUT:
                _flatten(inner, f"{prefix}.{key}", out)
    else:
        out[prefix] = value


def flat(entry: dict) -> dict:
    """A details read as {name: value}: id, layer, draw_order, details.<path>."""
    out = {"id": entry.get("id") or "", "layer": entry.get("layerIndex"),
           "draw_order": entry.get("drawIndex")}
    _flatten(entry.get("details") or {}, "details", out)
    return out


class _Names:
    """Shows a flattened value as the caller would name it."""

    def __init__(self, conn: ArchicadConnection, layers: dict[int, str]):
        self.conn = conn
        self.layers = layers
        self.tables: dict[str, dict[str, str]] = {}

    def _attribute(self, kind: str, guid: str) -> str:
        if kind not in self.tables:
            try:
                self.tables[kind] = {a["guid"].upper(): a["name"]
                                     for a in tapir_attributes(self.conn, kind)}
            except _ERRORS:
                self.tables[kind] = {}
        return self.tables[kind].get(guid.upper(), guid)

    def show(self, name: str, value):
        if name == "layer" and isinstance(value, int):
            return self.layers.get(value, value)
        if _is_ref(value):
            kind = ATTRIBUTE_FIELDS.get(name.rsplit(".", 1)[-1])
            return self._attribute(kind, value["guid"]) if kind else value["guid"]
        return value


def diff(before: dict, after: dict, names: _Names) -> dict[str, dict]:
    old, new = flat(before), flat(after)
    out = {}
    for name in [*old, *(k for k in new if k not in old)]:
        if old.get(name) != new.get(name):
            out[name] = {"current": names.show(name, old.get(name)),
                         "new": names.show(name, new.get(name))}
    return out


def _restore_ids(conn: ArchicadConnection, ids: dict[str, str]) -> dict[str, str]:
    """Write each element's own ID back; guid -> why it was not written."""
    try:
        property_id = resolve_property_ids(conn, [ELEMENT_ID]).get(ELEMENT_ID)
    except _ERRORS as exc:
        return dict.fromkeys(ids, f"the Element ID property could not be found "
                                  f"({error_fields(exc)['message']})")
    if property_id is None:
        return dict.fromkeys(ids, "Archicad did not find the Element ID property")
    items = [{"elementId": {"guid": g}, "propertyId": property_id,
              "propertyValue": {"type": "string", "status": "normal", "value": v}}
             for g, v in ids.items()]
    return _send(conn.official, "API.SetPropertyValuesOfElements", "elementPropertyValues",
                 items)


def _restore_layers(conn: ArchicadConnection, layers: dict[str, int]) -> dict[str, str]:
    items = [{"elementId": {"guid": g}, "details": {"layerIndex": index}}
             for g, index in layers.items()]
    return _send(conn.tapir, "SetDetailsOfElements", "elementsWithDetails", items)


def _send(post, command: str, key: str, items: list[dict]) -> dict[str, str]:
    refused: dict[str, str] = {}
    for start in range(0, len(items), PROPERTY_FETCH_CHUNK):
        chunk = items[start:start + PROPERTY_FETCH_CHUNK]
        try:
            results = post(command, {key: chunk}).get("executionResults") or []
        except _ERRORS as exc:
            for item in chunk:
                refused[item["elementId"]["guid"]] = (
                    f"{command} failed: {error_fields(exc)['message']}")
            continue
        for item, outcome in zip(chunk, results):
            if isinstance(outcome, dict) and outcome.get("success") is False:
                error = outcome.get("error") or {}
                refused[item["elementId"]["guid"]] = (
                    f"Archicad refused the write: {error.get('message', error)}")
    return refused


def _restore(conn: ArchicadConnection, p: FavoritePlan, before: dict[str, dict],
             after: dict[str, dict]) -> tuple[dict[str, int], list[dict], list[str]]:
    """(restored per field, not restored, GUIDs written) for what `keep` names."""
    attempts: dict[str, dict[str, object]] = {}
    refused: dict[str, dict[str, str]] = {}
    if "id" in p.keep:
        ids = {g: before[g].get("id") or "" for g in after
               if (after[g].get("id") or "") != (before[g].get("id") or "")}
        if ids:
            attempts["id"] = ids
            refused["id"] = _restore_ids(conn, ids)
    if "layer" in p.keep:
        layers = {g: before[g].get("layerIndex") for g in after
                  if after[g].get("layerIndex") != before[g].get("layerIndex")}
        if layers:
            attempts["layer"] = layers
            refused["layer"] = _restore_layers(conn, layers)
    written = list(dict.fromkeys(g for values in attempts.values() for g in values))
    if not written:
        return {}, [], []
    final = _read(conn, written, DETAIL_FIELDS)
    after.update(final)
    restored: dict[str, int] = {}
    missing: list[dict] = []
    detail_key = {"id": "id", "layer": "layerIndex"}
    for name, values in attempts.items():
        restored[name] = 0
        for g, wanted in values.items():
            read = final.get(g, {}).get(detail_key[name])
            if name == "id":
                read = read or ""
            if g in final and read == wanted:
                restored[name] += 1
            else:
                missing.append({"guid": g, "field": name, "reason": refused[name].get(
                    g, "Archicad answered success but the read shows the favourite's value")})
    return restored, missing, written


def commit(conn: ArchicadConnection, p: FavoritePlan) -> dict:
    guids = [e["guid"] for e in p.planned]
    result: dict = {"dry_run": False, **_favorite_fields(p), "requested": p.requested,
                    "applied": 0}
    if p.skipped:
        result["skipped"] = _group(p.skipped)
    if not guids:
        return result
    before = _read(conn, guids, DETAIL_FIELDS)
    payload = {"favoritesToApply": [{"elementId": {"guid": g}, "favorite": p.favorite}
                                    for g in guids],
               **{FLAGS[k]: v for k, v in p.flags.items()}}
    stopped = None
    try:
        response = conn.tapir("ApplyFavoritesToElements", payload) or {}
    except _ERRORS as exc:
        # Archicad may have acted before the error; the read says what changed.
        response, stopped = {}, exc
    results = response.get("executionResults") or []
    applied, failed, unconfirmed = [], [], []
    for i, guid in enumerate(guids):
        outcome = results[i] if i < len(results) else None
        if isinstance(outcome, dict) and outcome.get("success") is True:
            applied.append(guid)
        elif isinstance(outcome, dict) and outcome.get("success") is False:
            error = outcome.get("error") or {}
            code = error.get("code")
            failed.append({"guid": guid, "code": code, "favorite": p.favorite,
                           "message": _MESSAGES.get(code, error.get("message", str(error)))})
        else:
            unconfirmed.append(guid)

    landed = applied + unconfirmed
    after = {g: e for g, e in _read(conn, landed, DETAIL_FIELDS).items() if g in before}
    restored, not_restored, _ = _restore(conn, p, before, after)
    names = _Names(conn, p.layers)
    changes = {g: d for g in landed if g in after
               if (d := diff(before[g], after[g], names))}
    result["applied"] = len(applied)
    shown, rest = cap_list(list(changes))
    if shown:
        result["changed"] = {g: changes[g] for g in shown}
    if rest:
        result["changed_not_shown"] = rest
    unchanged = [g for g in applied if g in after and g not in changes]
    if unchanged:
        result["unchanged"], unchanged_rest = cap_list(unchanged)
        if unchanged_rest:
            result["unchanged_not_shown"] = unchanged_rest
    if failed:
        result["failed"] = group_failures(failed, detail_key="favorite")
    if restored:
        result["restored"] = restored
    if not_restored:
        result["not_restored"] = not_restored
    if unconfirmed:
        result["unconfirmed"] = unconfirmed
    if stopped is not None:
        result["stopped"] = error_fields(stopped)
    unread = [k for k in ("classifications", "categories", "properties") if p.flags.get(k)]
    notes = _notes(p.flags)
    if unread:
        notes.append(f"{', '.join(unread).capitalize()} were applied but are not read "
                     "back; check them with get_element_data.")
    if notes:
        result["notes"] = notes
    if p.window is not None:
        result["active_window"] = p.window
    return result
