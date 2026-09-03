"""search_definitions: fuzzy lookup over property and attribute definitions.

Definition reads only. Nothing here calls GetPropertyValuesOfElements, so this
tool cannot trigger the property-read crash.

Sources, live-probed on AC 29 / Tapir 1.5.9 (2026-09-03):

* Tapir ``GetAllProperties`` lists every definition (1619 built-in on a bare
  project, plus the custom ones) with group, name, value type, measure type,
  collection type, editability, expression flag and enum values. One call.
* The official ``GetAllPropertyNames`` knows 641 built-ins by their API name
  (``ModelView_LayerName``); ``GetPropertyIds`` maps all 641 to GUIDs that match
  Tapir's list. The other ~1000 built-ins Tapir lists have no API name and are
  addressed by GUID.
* Built-in Group/Name pairs are not unique (11 duplicates live), so the address
  handed out for a built-in is its API name, or its GUID.
* Attributes: ``GetAttributesByType`` accepts 10 types on AC 29 (MEPSystem and
  OperationProfile are refused with 4002), each detailed by its own command.
"""
from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection

KINDS = ("property", "attribute", "any")

# attribute type -> official detail command. All ten answered live on AC 29.
ATTRIBUTE_DETAIL_COMMANDS: dict[str, str] = {
    "Layer": "API.GetLayerAttributes",
    "Line": "API.GetLineAttributes",
    "Fill": "API.GetFillAttributes",
    "Composite": "API.GetCompositeAttributes",
    "Surface": "API.GetSurfaceAttributes",
    "LayerCombination": "API.GetLayerCombinationAttributes",
    "ZoneCategory": "API.GetZoneCategoryAttributes",
    "Profile": "API.GetProfileAttributes",
    "PenTable": "API.GetPenTableAttributes",
    "BuildingMaterial": "API.GetBuildingMaterialAttributes",
}

_BUILTIN_TYPES = frozenset({"StaticBuiltIn", "DynamicBuiltIn"})


@dataclass
class Definition:
    kind: str                       # "property" | "attribute"
    name: str                       # display name
    address: str                    # what the other tools accept
    haystack: list[str] = field(default_factory=list)   # searchable strings
    extra: dict = field(default_factory=dict)

    def to_dict(self, score: float) -> dict:
        d = {"kind": self.kind, "name": self.name, "score": round(score, 3)}
        if self.kind == "property":
            d["property"] = self.address
        else:
            d["attribute_type"] = self.extra.get("attribute_type")
        d.update({k: v for k, v in self.extra.items() if k != "attribute_type"})
        return d


# ---------- fuzzy matching ----------

def fold(text: str) -> str:
    """Casefold and strip diacritics, so 'splosno' finds 'Splošno'."""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).casefold()


def tokens(text: str) -> list[str]:
    out, cur = [], []
    for ch in fold(text):
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


def _token_score(query_token: str, hay_tokens: list[str], hay_text: str) -> float:
    best = 0.0
    for ht in hay_tokens:
        if ht == query_token:
            return 1.0
        if ht.startswith(query_token):
            best = max(best, 0.9)
        elif query_token in ht:
            best = max(best, 0.75)
        elif len(query_token) >= 4:
            ratio = difflib.SequenceMatcher(None, query_token, ht).ratio()
            if ratio >= 0.8:
                best = max(best, ratio * 0.7)
    if best == 0.0 and query_token in hay_text:
        best = 0.6   # spans a token boundary ("firerating" vs "fire rating")
    return best


def score(query: str, haystack: Iterable[str]) -> float:
    """0 when any query token fails to match; else the mean token score."""
    q_tokens = tokens(query)
    if not q_tokens:
        return 0.0
    hay_texts = [fold(h) for h in haystack]
    hay_tokens = [t for h in hay_texts for t in tokens(h)]
    joined = " ".join(hay_texts).replace(" ", "")
    total = 0.0
    for qt in q_tokens:
        s = _token_score(qt, hay_tokens, joined)
        if s == 0.0:
            return 0.0
        total += s
    return total / len(q_tokens)


# ---------- sources ----------

def _property_definitions(conn: ArchicadConnection, notes: list[str]) -> list[Definition]:
    if conn.tapir_available():
        return _properties_via_tapir(conn)
    notes.append("Tapir add-on absent: property results come from the official "
                 "API and lack measure type, collection type and enum values.")
    return _properties_via_official(conn)


def _builtin_api_names(conn: ArchicadConnection) -> dict[str, str]:
    """property GUID -> official non-localized name, for the built-ins that have one."""
    names = conn.official("API.GetAllPropertyNames").get("properties", [])
    builtin = [p for p in names if p.get("type") == "BuiltIn"]
    if not builtin:
        return {}
    ids = conn.official("API.GetPropertyIds", {"properties": builtin}).get("properties", [])
    out = {}
    for p, item in zip(builtin, ids):
        guid = item.get("propertyId", {}).get("guid")
        if guid:
            out[guid] = p["nonLocalizedName"]
    return out


def _properties_via_tapir(conn: ArchicadConnection) -> list[Definition]:
    props = conn.tapir("GetAllProperties").get("properties", [])
    api_names = _builtin_api_names(conn)
    out = []
    for p in props:
        guid = p.get("propertyId", {}).get("guid", "")
        group = p.get("propertyGroupName", "")
        name = p.get("propertyName", "")
        builtin = p.get("propertyType") in _BUILTIN_TYPES
        if builtin:
            address = api_names.get(guid, guid)
        else:
            address = f"{group}/{name}"
        enum_values = [e.get("enumValue", {}).get("displayValue", "")
                       for e in p.get("possibleEnumValues", [])]
        extra = {
            "group": group,
            "builtin": builtin,
            "value_type": p.get("propertyValueType"),
            "measure_type": p.get("propertyMeasureType"),
            "collection": p.get("propertyCollectionType"),
            "editable": bool(p.get("propertyIsEditable")),
            "expression_based": bool(p.get("isExpressionBased")),
            "guid": guid,
        }
        if enum_values:
            extra["enum_values"] = enum_values
        hay = [f"{group}/{name}", *enum_values]
        if builtin and address != guid:
            hay.append(address)
        out.append(Definition("property", f"{group}/{name}", address, hay, extra))
    return out


def _properties_via_official(conn: ArchicadConnection) -> list[Definition]:
    names = conn.official("API.GetAllPropertyNames").get("properties", [])
    if not names:
        return []
    ids = conn.official("API.GetPropertyIds", {"properties": names}).get("properties", [])
    pairs = [(n, i["propertyId"]) for n, i in zip(names, ids) if "propertyId" in i]
    details = conn.official("API.GetDetailsOfProperties",
                            {"properties": [{"propertyId": pid} for _, pid in pairs]})
    out = []
    for (n, pid), d in zip(pairs, details.get("propertyDefinitions", [])):
        d = d.get("propertyDefinition", {})
        group = d.get("group", {}).get("name", "")
        name = d.get("name", "")
        builtin = n.get("type") == "BuiltIn"
        address = n.get("nonLocalizedName") if builtin else f"{group}/{name}"
        extra = {"group": group, "builtin": builtin, "value_type": d.get("type"),
                 "editable": bool(d.get("isEditable")), "guid": pid.get("guid")}
        hay = [f"{group}/{name}"] + ([address] if builtin else [])
        out.append(Definition("property", f"{group}/{name}", address, hay, extra))
    return out


def _attribute_definitions(conn: ArchicadConnection, notes: list[str]) -> list[Definition]:
    out = []
    for attr_type, command in ATTRIBUTE_DETAIL_COMMANDS.items():
        try:
            ids = conn.official("API.GetAttributesByType", {"attributeType": attr_type})
            attribute_ids = ids.get("attributeIds", [])
            if not attribute_ids:
                continue
            response = conn.official(command, {"attributeIds": attribute_ids})
        except APIErrorBase as exc:  # one refused type must not sink the rest
            notes.append(f"attribute type {attr_type} refused: {exc}")
            continue
        for item in response.get("attributes", []):
            inner = next(iter(item.values()), {})
            if not isinstance(inner, dict) or "name" not in inner:
                continue
            name = inner["name"]
            out.append(Definition("attribute", name, name, [f"{attr_type}/{name}"],
                                  {"attribute_type": attr_type}))
    return out


# ---------- the tool ----------

def search_definitions(conn: ArchicadConnection, query: str, kind: str = "any",
                       alternatives: list[str] | None = None,
                       editable_only: bool = False, limit: int = 25) -> dict:
    if kind not in KINDS:
        return {"error": f"kind must be one of {KINDS}, got {kind!r}"}
    terms = [query, *(alternatives or [])]
    terms = [t for t in terms if isinstance(t, str) and t.strip()]
    if not terms:
        return {"error": "query must be a non-empty string"}
    if len(alternatives or []) > 6:
        return {"error": "at most 6 alternatives"}
    limit = max(1, min(int(limit), 200))

    notes: list[str] = []
    definitions: list[Definition] = []
    if kind in ("property", "any"):
        definitions += _property_definitions(conn, notes)
    if kind in ("attribute", "any"):
        definitions += _attribute_definitions(conn, notes)

    scored = []
    for d in definitions:
        if editable_only and d.kind == "property" and not d.extra.get("editable"):
            continue
        best = max(score(t, d.haystack) for t in terms)
        if best > 0:
            scored.append((best, d))
    scored.sort(key=lambda x: (-x[0], x[1].kind, x[1].name))

    result = {"query": query, "total_matches": len(scored),
              "matches": [d.to_dict(s) for s, d in scored[:limit]]}
    if len(scored) > limit:
        result["truncated"] = True
    if notes:
        result["notes"] = notes
    return result
