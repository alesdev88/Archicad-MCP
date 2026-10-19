"""Recorded and schema-derived Tapir answers for the modify_elements tests.

Each JSON file carries a "_source" line saying whether it was recorded live on
MCP-Test or written from the bundled schemas; load() drops it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> dict:
    data = json.loads((_DIR / name).read_text(encoding="utf-8"))
    data.pop("_source", None)
    return data


def all_details() -> dict[str, dict]:
    """GUID -> GetDetailsOfElements item, recorded ones and schema-derived ones."""
    out = dict(load("details_recorded.json")["elements"])
    out.update(load("details_schema.json")["elements"])
    return copy.deepcopy(out)


def layers_answer(locked: set[int] = frozenset(), hidden: set[int] | None = None):
    """A Tapir GetLayers handler over the recorded layer list.

    Layers 33 and 56 answer as recorded; the others answer the same shape with
    both flags off. `locked` and `hidden` override the flags by index.
    """
    by_guid = {a["attributeId"]["guid"]: a for a in load("layers_by_type.json")["attributes"]}
    recorded = {item["index"]: item for item in load("get_layers.json")["layers"]}

    def handler(params):
        out = []
        for entry in params["attributeIds"]:
            attr = by_guid[entry["attributeId"]["guid"]]
            index = attr["index"]
            item = dict(recorded.get(index) or {
                "attributeId": attr["attributeId"], "index": index, "name": attr["name"],
                "isHidden": False, "isLocked": False})
            if index in locked:
                item["isLocked"] = True
            if hidden is not None:
                item["isHidden"] = index in hidden
            out.append(item)
        return {"layers": out}
    return handler
