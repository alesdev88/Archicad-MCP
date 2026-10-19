from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.attribute_types import (
    LAYER_LIST,
    REFERENCE_FIELDS,
    TYPES,
    AttrType,
    lookup_type,
)


def _unknown_type(attribute_type: str) -> dict:
    return {"error": f"Unknown attribute_type '{attribute_type}'. Valid: {sorted(TYPES)}."}


def list_attributes(conn: ArchicadConnection, attribute_type: str, details: bool = False,
                    names: list[str] | None = None, limit: int | None = None,
                    offset: int = 0) -> dict:
    attr = lookup_type(attribute_type)
    if attr is None:
        return _unknown_type(attribute_type)
    if details:
        return _details(conn, attr, names, limit, offset)
    ids = conn.official("API.GetAttributesByType", {"attributeType": attr.name})
    attribute_ids = ids.get("attributeIds", [])
    if not attribute_ids:
        return {"attribute_type": attr.name, "names": []}
    response = conn.official(attr.official_detail, {"attributeIds": attribute_ids})
    found = []
    for item in response.get("attributes", []):
        # each item is {"<type>Attribute": {..., "name": ...}}
        inner = next(iter(item.values()), {})
        if isinstance(inner, dict) and "name" in inner:
            found.append(inner["name"])
    return {"attribute_type": attr.name, "names": found}


def _details(conn: ArchicadConnection, attr: AttrType, names: list[str] | None,
             limit: int | None, offset: int) -> dict:
    from archicad_mcp.tapir_requirements import require_tapir_command
    refusal = require_tapir_command(conn, attr.get, "list_attributes")
    if refusal:
        return refusal
    table = AttributeNames(conn)
    headers = table.headers(attr.name)
    result: dict = {"attribute_type": attr.name}
    if names:
        known = {h["name"] for h in headers}
        headers = [h for h in headers if h["name"] in set(names)]
        missing = [n for n in names if n not in known]
        if missing:
            result["not_found"] = missing
    page = limit or attr.page
    chosen = headers[offset:offset + page]
    records = read_records(conn, attr, [h["guid"] for h in chosen])
    result["total"] = len(headers)
    result["attributes"] = [
        named_references(record, attr.name, table) if record is not None
        else {"name": header["name"], "error": "Archicad did not return this attribute"}
        for header, record in zip(chosen, records)]
    if offset + page < len(headers):
        result["next_offset"] = offset + page
    return result


def read_records(conn: ArchicadConnection, attr: AttrType,
                 guids: list[str]) -> list[dict | None]:
    """The type's Get* record per GUID, in order; None where Archicad answered an
    error. Layer combinations are unwrapped and get `index` like the others."""
    if not guids:
        return []
    response = conn.tapir(attr.get, {attr.get_in: [{"attributeId": {"guid": g}}
                                                   for g in guids]})
    out: list[dict | None] = []
    for item in response.get(attr.get_out, []):
        if not isinstance(item, dict) or "error" in item:
            out.append(None)
            continue
        if "layerCombination" in item:
            item = dict(item["layerCombination"])
            item["index"] = item.pop("attributeIndex", None)
        out.append(item)
    return out + [None] * (len(guids) - len(out))


class AttributeNames:
    """Index, name and GUID of every attribute of a type, read once per type."""

    def __init__(self, conn: ArchicadConnection):
        self.conn = conn
        self._headers: dict[str, list[dict]] = {}

    def headers(self, attr_type: str) -> list[dict]:
        if attr_type not in self._headers:
            self._headers[attr_type] = tapir_attributes(self.conn, attr_type)
        return self._headers[attr_type]

    def forget(self, attr_type: str) -> None:
        self._headers.pop(attr_type, None)

    def by_guid(self, attr_type: str, guid: str) -> dict | None:
        # Tapir answers GUIDs uppercase; a caller may send them in any case.
        key = guid.upper() if isinstance(guid, str) else guid
        return next((h for h in self.headers(attr_type) if h["guid"].upper() == key), None)

    def by_index(self, attr_type: str, index: int) -> dict | None:
        return next((h for h in self.headers(attr_type) if h["index"] == index), None)

    def by_name(self, attr_type: str, name: str) -> dict | None:
        return next((h for h in self.headers(attr_type) if h["name"] == name), None)

    def label(self, attr_type: str, value, form: str):
        """A reference as the name it points at, or what it was when it is not one."""
        if form == "index" and isinstance(value, int) and not isinstance(value, bool):
            header = self.by_index(attr_type, value)
            return header["name"] if header else f"<missing {attr_type} {value}>"
        if isinstance(value, str):
            # A name stays a name; a GUID string becomes the name it points at.
            header = self.by_guid(attr_type, value)
            return header["name"] if header else value
        guid = _guid_of(value)
        if guid is None:
            return value
        header = self.by_guid(attr_type, guid)
        return header["name"] if header else f"<missing {attr_type} {guid}>"


def _guid_of(value) -> str | None:
    """The GUID of {"attributeId": {"guid"}} or {"guid"}; None for anything else."""
    if isinstance(value, dict):
        inner = value.get("attributeId", value)
        if isinstance(inner, dict) and isinstance(inner.get("guid"), str):
            return inner["guid"]
    return None


def named_references(record: dict, attr_type: str, names: AttributeNames) -> dict:
    """The record with every attribute reference replaced by the name it points at."""
    return _named(record, attr_type, names, in_layer_list=False)


def _named(value, attr_type: str, names: AttributeNames, in_layer_list: bool):
    if isinstance(value, list):
        return [_named(v, attr_type, names, in_layer_list) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in REFERENCE_FIELDS:
            ref_type, form = REFERENCE_FIELDS[key]
            out[key] = names.label(ref_type or attr_type, item, form)
        elif in_layer_list and key == "attributeId":
            out[key] = names.label("Layer", item, "guid")
        else:
            out[key] = _named(item, attr_type, names,
                              attr_type == "LayerCombination" and key == LAYER_LIST)
    return out


def tapir_attributes(conn: ArchicadConnection, attribute_type: str) -> list[dict]:
    """[{index, name, guid}] of every attribute of the type, from Tapir.

    The index is what element details hold (GetDetailsOfElements layerIndex,
    a mesh's lineTypeIndex) and what SetDetailsOfElements takes.
    """
    response = conn.tapir("GetAttributesByType", {"attributeType": attribute_type})
    return [{"index": a["index"], "name": a["name"], "guid": a["attributeId"]["guid"]}
            for a in response.get("attributes", [])]


def layer_table(conn: ArchicadConnection) -> dict[int, dict]:
    """Layer index -> {name, guid, isHidden, isLocked}."""
    layers = tapir_attributes(conn, "Layer")
    if not layers:
        return {}
    response = conn.tapir("GetLayers", {
        "attributeIds": [{"attributeId": {"guid": a["guid"]}} for a in layers],
        "fields": ["isHidden", "isLocked"]})
    flags = {item.get("index"): item for item in response.get("layers", [])
             if isinstance(item, dict) and "index" in item}
    return {a["index"]: {"name": a["name"], "guid": a["guid"],
                         "isHidden": bool(flags.get(a["index"], {}).get("isHidden")),
                         "isLocked": bool(flags.get(a["index"], {}).get("isLocked"))}
            for a in layers}
