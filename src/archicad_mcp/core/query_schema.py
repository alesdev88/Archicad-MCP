"""Typed input for find_elements.

The criteria structure used to be `list[dict]`, which made the whole language
prose in the tool description and left every typo to fail at runtime. These
models give the MCP schema enums for the element types (read from the bundled
Tapir definitions, the same enum GetElementsByType accepts, plus "all") and
for the operators, so a client validates before it sends.

The models are the wire shape only. Evaluation still goes through
criteria.parse_groups on plain dicts, so the rules engine keeps importing
criteria without pydantic or the gateway definitions in the way.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from archicad_mcp.criteria import OPERATORS

_DEFINITIONS = Path(__file__).parent.parent / "gateway" / "definitions" / "common_schema_definitions.js"


@lru_cache(maxsize=1)
def element_type_names() -> tuple[str, ...]:
    """Archicad element type names from the bundled schema, 'all' first."""
    text = _DEFINITIONS.read_text(encoding="utf-8")
    text = re.sub(r"^\s*(?:var|const|let)\s+\w+\s*=\s*", "", text, count=1).rstrip("; \n")
    names = json.loads(text)["ElementType"]["enum"]
    return ("all", *names)


ElementTypeName = Literal[element_type_names()]  # type: ignore[valid-type]
Operator = Literal[tuple(sorted(OPERATORS))]  # type: ignore[valid-type]
Value = str | int | float | bool | list[str] | list[int] | list[float]


class ComparisonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property: str = Field(description=(
        "'Group/Name' for a user property, the API name for a built-in "
        "(e.g. ModelView_LayerName), a property GUID, "
        "'classification:<System name>' for the element's classification "
        "item, or 'story' for the home story index (ground floor 0, basements "
        "negative). search_definitions finds the exact address."))
    operator: Operator = Field(description=(
        "Binary operators need a value: equal, not_equal, less, greater, "
        "less_or_equal, greater_or_equal; contains, does_not_contain, "
        "starts_with, ends_with (strings, case-insensitive); is_in_branch_of, "
        "is_direct_child_of, is_not_in_branch_of, is_not_direct_child_of "
        "(classification items, by item ID or GUID). Unary operators take no "
        "value: has_value, has_no_value, is_user_undefined, "
        "is_not_user_undefined, available, not_available."))
    value: Value | None = Field(default=None, description=(
        "The value for a binary operator. Numeric values in SI base units: m, "
        "m2, m3, radian (3000 mm -> 3). Enum values are their display text. "
        "Story values are integers. Classification values are item IDs ('Wall') "
        "or GUIDs."))


class GroupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_types: list[ElementTypeName] | None = Field(default=None, description=(
        "Archicad element type names to restrict this group to. Omit for every "
        "type; 'all' says so explicitly."))
    element_types_operator: Literal["is", "is_not"] = Field(default="is", description=(
        "'is' keeps the listed types, 'is_not' keeps every other type."))
    logical_operator: Literal["and", "or"] = Field(default="and", description=(
        "How this group's comparisons combine."))
    comparisons: list[ComparisonSpec] = Field(default_factory=list, description=(
        "Property checks every candidate in this group is tested against."))


def groups_to_dicts(groups: list[GroupSpec]) -> list[dict]:
    """The plain shape criteria.parse_groups takes. None fields are dropped so
    an omitted value stays 'no value' for the unary operators."""
    out = []
    for g in groups:
        d = g.model_dump(exclude_none=True)
        d["comparisons"] = [c.model_dump(exclude_none=True) for c in g.comparisons]
        out.append(d)
    return out
