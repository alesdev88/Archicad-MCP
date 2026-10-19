"""Addresses of element data: one syntax for get_element_data and set_element_data.

The prefix decides the kind; anything without one is a property, as before
('Group/Name', a built-in non-localized name, or a property GUID).
find_elements already reserves 'classification:', so the other kinds follow
it. The design is docs/superpowers/specs/2026-10-19-element-data-kinds-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

PROPERTY = "property"
CLASSIFICATION = "classification"
GDL = "gdl"
MARKER = "marker"
IFC = "ifc"

_PREFIXES = {"classification:": CLASSIFICATION, "gdl:": GDL, "marker:": MARKER, "ifc:": IFC}
_WHAT = {CLASSIFICATION: "a classification system name", GDL: "a GDL parameter name",
         MARKER: "a marker parameter name"}
WILDCARD = "*"
IFC_FORM = "write ifc:<Pset>.<Name>, for example ifc:Pset_DoorCommon.FireRating"


@dataclass(frozen=True)
class Address:
    text: str      # as the caller gave it; results are keyed by it
    kind: str
    key: str       # property name, system label, parameter name, or "Pset.Name"
    wildcard: bool


def parse_address(text: str) -> Address | str:
    """The address, or why it is not one. A blank address stays a property,
    as it was before addresses had prefixes: it reads as missing."""
    if not isinstance(text, str):
        return "the address is not text"
    for prefix, kind in _PREFIXES.items():
        if text[:len(prefix)].lower() == prefix:
            key = text[len(prefix):].strip()
            if kind == IFC and key != WILDCARD:
                pset, _, name = key.partition(".")
                if not pset.strip() or not name.strip():
                    return f"'{text}' is not an IFC address: {IFC_FORM}"
            elif not key:
                return f"'{text}' needs {_WHAT[kind]} after the prefix"
            return Address(text=text, kind=kind, key=key, wildcard=key == WILDCARD)
    return Address(text=text, kind=PROPERTY, key=text, wildcard=False)


def ifc_parts(address: Address) -> tuple[str, str]:
    """(property set, property name) of an ifc: address, split at the first dot."""
    pset, _, name = address.key.partition(".")
    return pset, name
