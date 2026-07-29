from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.schemes.model import (
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Scheme,
)


def property_index(conn: ArchicadConnection) -> dict[str, str]:
    """'Group/Name' to GUID for every property defined in the open project.

    Reads property DEFINITIONS via Tapir GetAllProperties. This is not
    GetPropertyValuesOfElements and does not sit on the crash path documented
    in docs/known-issues.md.

    Tapir is required: there is no other way to resolve a property binding,
    so nothing to degrade to. If the add-on is absent, conn.tapir() raises
    ArchicadUnavailableError itself, naming the missing command and how to
    install it; that propagates through this function untouched.
    """
    response = conn.tapir("GetAllProperties", None)
    index: dict[str, str] = {}
    # Guard against non-list properties value (e.g. null or a dict) to avoid
    # unguarded TypeErrors/AttributeErrors that skip the tool layer's error
    # envelope. Treating a malformed response as empty properties is simpler
    # than raising a custom error: validation will report unresolved bindings.
    properties = response.get("properties", [])
    if not isinstance(properties, list):
        properties = []
    for item in properties:
        guid = (item.get("propertyId") or {}).get("guid")
        if not guid:
            continue
        group = item.get("propertyGroupName", "")
        name = item.get("propertyName", "")
        index[f"{group}/{name}"] = guid
    return index


def validate_scheme(conn: ArchicadConnection, scheme: Scheme) -> list[dict]:
    """Check every column's binding against the open project's properties.

    A column bound to a property GUID that no longer exists in this project
    is an error: the scheme cannot possibly show correct data for it. A
    caption that does not mention what it is bound to is only a warning: the
    binding itself still resolves, but the label may mislead whoever reads
    the schedule. Built-in columns (Quantity and the like) are not checked:
    there is no property or parameter to resolve, so there is nothing to get
    out of sync.
    """
    index = property_index(conn)
    known_guids = set(index.values())
    guid_to_name = {guid: name for name, guid in index.items()}
    findings: list[dict] = []

    for column in scheme.columns:
        binding = column.binding
        if binding.kind == KIND_PROPERTY:
            guid = binding.property_guid
            if guid == NULL_GUID or guid not in known_guids:
                findings.append({
                    "severity": "error", "column": column.caption,
                    "message": f"Bound to property {guid}, which does not exist in "
                               "this project. The scheme came from a project with "
                               "different property definitions.",
                })
                continue
            resolved = guid_to_name[guid]
            short = resolved.split("/", 1)[-1]
            if short and short.lower() not in column.caption.lower():
                findings.append({
                    "severity": "warning", "column": column.caption,
                    "message": f"Caption does not mention the bound property "
                               f"{resolved!r}. Check the column shows what it claims.",
                })
        elif binding.kind == KIND_GDL_PARAM:
            bound = binding.property_name
            if bound and bound.lower() not in column.caption.lower():
                findings.append({
                    "severity": "warning", "column": column.caption,
                    "message": f"Caption does not mention the bound GDL parameter "
                               f"{bound!r}. Check the column shows what it claims.",
                })
    return findings
