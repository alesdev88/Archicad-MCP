"""The criteria language behind find_elements and rule scoping.

Pure: no transport, no Archicad. Everything here works on already-fetched
values, so the rules engine can import it without dragging in a connection,
and the offline tests can drive every operator without a running Archicad.

A query is a list of groups. Inside a group the comparisons combine with
``and`` (default) or ``or``; the groups themselves combine with ``or``. Each
group may also restrict the element types it looks at.

A comparison addresses one of three kinds of element data through a single
``property`` string:

* ``"Group/Name"`` (a user-defined property), ``"ModelView_LayerName"`` (a
  built-in property by its API name), or a property GUID.
* ``"classification:<System name>"``: the element's classification item in
  that system. Values are item IDs as shown in Archicad ("Wall") or item GUIDs.
* ``"story"``: the home story as Archicad's story index (Tapir floorIndex):
  ground floor 0, basements negative.

Live-verified value semantics (AC 29, JSON API schema bundled in gateway/):
a property cell carries ``status`` normal / userUndefined / notAvailable, and
only a normal cell has a ``value``. Those three statuses plus "the value is
empty" are the four senses of empty the unary operators tell apart.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

CLASSIFICATION_PREFIX = "classification:"
STORY = "story"

ORDERING_OPERATORS = frozenset({"equal", "not_equal", "less", "greater",
                                "less_or_equal", "greater_or_equal"})
STRING_OPERATORS = frozenset({"contains", "does_not_contain", "starts_with", "ends_with"})
BRANCH_OPERATORS = frozenset({"is_in_branch_of", "is_direct_child_of",
                              "is_not_in_branch_of", "is_not_direct_child_of"})
UNARY_OPERATORS = frozenset({"has_value", "has_no_value", "is_user_undefined",
                             "is_not_user_undefined", "available", "not_available"})
BINARY_OPERATORS = ORDERING_OPERATORS | STRING_OPERATORS | BRANCH_OPERATORS
OPERATORS = BINARY_OPERATORS | UNARY_OPERATORS

ELEMENT_TYPES_OPERATORS = ("is", "is_not")
LOGICAL_OPERATORS = ("and", "or")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Tolerance for numeric equality. Lengths come back from Archicad as doubles
# in metres; 3000 mm typed as 3 must equal a wall that measures 2.9999999999.
_ABS_TOL = 1e-6
_REL_TOL = 1e-9


class CriteriaError(ValueError):
    """The query is malformed. str(exc) says what to fix."""


def is_guid(text: str) -> bool:
    return bool(_UUID.match(text))


# ---------- cells: one element's value for one comparison ----------

@dataclass(frozen=True)
class Cell:
    """A normalised value with its availability status.

    status is one of "normal", "userUndefined", "notAvailable". value is only
    meaningful when status is "normal"; it is already unwrapped: enum values
    become their display strings, enum lists become string lists.
    """
    status: str
    value: Any = None

    @property
    def usable(self) -> bool:
        """The has_value sense: a normal cell whose value is not empty."""
        if self.status != "normal":
            return False
        v = self.value
        if v is None:
            return False
        if isinstance(v, (str, list, tuple, dict)) and len(v) == 0:
            return False
        return True

    @classmethod
    def from_api(cls, raw: dict | None) -> "Cell":
        """From a propertyValue dict as GetPropertyValuesOfElements returns it.

        An error cell ({} after extract.fetch_property_cells strips the error
        envelope) means the property is not available on that element.
        """
        if not raw or "status" not in raw:
            return cls("notAvailable")
        status = raw.get("status") or "notAvailable"
        if status != "normal":
            return cls(status)
        return cls("normal", _unwrap(raw.get("value")))

    @classmethod
    def from_value(cls, value: Any) -> "Cell":
        """From a plain value, as a rule snapshot carries it. None means the
        value is missing; the snapshot cannot tell userUndefined from
        notAvailable, so both collapse to userUndefined here."""
        if value is None:
            return cls("userUndefined")
        return cls("normal", _unwrap(value))


def _unwrap(value: Any) -> Any:
    """Enum ids ({displayValue} / {nonLocalizedValue}) become strings."""
    if isinstance(value, dict):
        for key in ("displayValue", "nonLocalizedValue"):
            if key in value:
                return value[key]
        return value
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


# ---------- classification tree ----------

class ClassificationTree:
    """One classification system's items, as GetAllClassificationsInSystem
    returns them, indexed for branch tests."""

    def __init__(self, items: Iterable[dict]):
        self._parent: dict[str, str | None] = {}
        self._id_of: dict[str, str] = {}
        self._guid_of_id: dict[str, str] = {}
        self._walk(items, None)

    def _walk(self, items: Iterable[dict], parent: str | None) -> None:
        for wrapper in items:
            item = wrapper.get("classificationItem", wrapper)
            guid = item.get("classificationItemId", {}).get("guid")
            if not guid:
                continue
            self._parent[guid] = parent
            item_id = item.get("id") or ""
            self._id_of[guid] = item_id
            # First occurrence wins; ids are unique within a system in practice.
            self._guid_of_id.setdefault(item_id.casefold(), guid)
            self._walk(item.get("children", []), guid)

    def __contains__(self, guid: str) -> bool:
        return guid in self._parent

    def resolve(self, ref: str) -> str | None:
        """An item GUID or its ID text ("Wall") -> GUID, or None."""
        if ref in self._parent:
            return ref
        return self._guid_of_id.get(ref.casefold())

    def id_of(self, guid: str) -> str | None:
        return self._id_of.get(guid)

    def parent(self, guid: str) -> str | None:
        return self._parent.get(guid)

    def ancestors(self, guid: str) -> list[str]:
        """Strict ancestors, nearest first."""
        out = []
        cur = self._parent.get(guid)
        while cur:
            out.append(cur)
            cur = self._parent.get(cur)
        return out


# ---------- query structure ----------

@dataclass(frozen=True)
class Comparison:
    property: str
    operator: str
    value: Any = None

    @property
    def kind(self) -> str:
        if self.property == STORY:
            return "story"
        if self.property.startswith(CLASSIFICATION_PREFIX):
            return "classification"
        return "property"

    @property
    def system(self) -> str:
        """The classification system name, for kind == 'classification'."""
        return self.property[len(CLASSIFICATION_PREFIX):]

    @property
    def is_unary(self) -> bool:
        return self.operator in UNARY_OPERATORS


@dataclass(frozen=True)
class CriteriaGroup:
    comparisons: tuple[Comparison, ...] = ()
    element_types: tuple[str, ...] = ()
    element_types_operator: str = "is"
    logical_operator: str = "and"

    def type_matches(self, element_type: str) -> bool:
        if not self.element_types:
            return True
        hit = element_type in self.element_types
        return hit if self.element_types_operator == "is" else not hit


def parse_comparison(raw: Any, where: str = "comparison") -> Comparison:
    if not isinstance(raw, dict):
        raise CriteriaError(f"{where}: expected an object with 'property' and "
                            f"'operator', got {raw!r}")
    prop = raw.get("property")
    if not isinstance(prop, str) or not prop.strip():
        raise CriteriaError(f"{where}: 'property' must be a non-empty string "
                            "('Group/Name', a built-in API name, a property GUID, "
                            "'classification:<System>' or 'story')")
    prop = prop.strip()
    op = raw.get("operator")
    if op not in OPERATORS:
        raise CriteriaError(f"{where}: unknown operator {op!r}. Known: "
                            f"{', '.join(sorted(OPERATORS))}")
    has_value = "value" in raw and raw["value"] is not None
    if op in UNARY_OPERATORS and has_value:
        raise CriteriaError(f"{where}: operator '{op}' takes no 'value'")
    if op in BINARY_OPERATORS and not has_value:
        raise CriteriaError(f"{where}: operator '{op}' needs a 'value'")
    kind = Comparison(prop, op).kind
    if op in BRANCH_OPERATORS and kind != "classification":
        raise CriteriaError(f"{where}: '{op}' only applies to "
                            "'classification:<System>' properties")
    if kind == "classification" and not Comparison(prop, op).system.strip():
        raise CriteriaError(f"{where}: 'classification:' needs a system name after the colon")
    if kind == "classification" and op in (STRING_OPERATORS | {"less", "greater", "less_or_equal", "greater_or_equal"}):
        raise CriteriaError(f"{where}: '{op}' does not apply to a classification; "
                            "use equal, not_equal, the branch operators, or has_value/has_no_value")
    if kind == "story":
        if op in STRING_OPERATORS | BRANCH_OPERATORS:
            raise CriteriaError(f"{where}: '{op}' does not apply to 'story'")
        if has_value and (isinstance(raw["value"], bool) or not isinstance(raw["value"], (int, float))):
            raise CriteriaError(f"{where}: 'story' compares against a 0-based integer index")
    unknown = set(raw) - {"property", "operator", "value"}
    if unknown:
        raise CriteriaError(f"{where}: unknown keys {sorted(unknown)}")
    return Comparison(prop, op, raw.get("value") if has_value else None)


def parse_groups(raw: Any) -> list[CriteriaGroup]:
    if not isinstance(raw, list) or not raw:
        raise CriteriaError("'groups' must be a non-empty list; pass one group "
                            "for a simple query")
    out = []
    for i, g in enumerate(raw):
        where = f"groups[{i}]"
        if not isinstance(g, dict):
            raise CriteriaError(f"{where}: expected an object")
        unknown = set(g) - {"comparisons", "element_types", "element_types_operator",
                            "logical_operator"}
        if unknown:
            raise CriteriaError(f"{where}: unknown keys {sorted(unknown)}")
        types = g.get("element_types") or []
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list) or not all(isinstance(t, str) and t for t in types):
            raise CriteriaError(f"{where}: 'element_types' must be a list of type names")
        # "all" is an explicit "every type", which also makes a group with no
        # comparisons a deliberate whole-plan listing rather than a mistake.
        explicit_all = any(t.casefold() == "all" for t in types)
        if explicit_all:
            types = []
        top = g.get("element_types_operator", "is")
        if top not in ELEMENT_TYPES_OPERATORS:
            raise CriteriaError(f"{where}: 'element_types_operator' must be 'is' or 'is_not'")
        lop = g.get("logical_operator", "and")
        if lop not in LOGICAL_OPERATORS:
            raise CriteriaError(f"{where}: 'logical_operator' must be 'and' or 'or'")
        comps = g.get("comparisons") or []
        if not isinstance(comps, list):
            raise CriteriaError(f"{where}: 'comparisons' must be a list")
        parsed = tuple(parse_comparison(c, f"{where}.comparisons[{j}]")
                       for j, c in enumerate(comps))
        if not parsed and not types and not explicit_all:
            raise CriteriaError(f"{where}: an empty group would match every element; "
                                "give it element_types (\"all\" to mean every type) "
                                "or comparisons")
        out.append(CriteriaGroup(parsed, tuple(types), top, lop))
    return out


# ---------- evaluation ----------

def _fold(text: Any) -> str:
    return str(text).casefold()


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _equal(a: Any, b: Any) -> bool:
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return math.isclose(na, nb, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(b, str) and _fold(b) in ("true", "false")
                          and (a is (_fold(b) == "true")))
    if isinstance(a, list):
        if isinstance(b, list):
            return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
        return False
    return _fold(a) == _fold(b)


def _order(a: Any, b: Any) -> int | None:
    """-1, 0, 1, or None when the two are not comparable."""
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        if math.isclose(na, nb, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
            return 0
        return -1 if na < nb else 1
    if isinstance(a, str) and isinstance(b, str):
        fa, fb = _fold(a), _fold(b)
        return 0 if fa == fb else (-1 if fa < fb else 1)
    return None


def _string_op(op: str, actual: Any, wanted: Any) -> bool:
    if isinstance(actual, list):
        # A list matches when any member does (a multi-choice enum, say).
        hits = [_string_op(op, item, wanted) for item in actual]
        return all(hits) if op == "does_not_contain" else any(hits)
    a, w = _fold(actual), _fold(wanted)
    if op == "contains":
        return w in a
    if op == "does_not_contain":
        return w not in a
    if op == "starts_with":
        return a.startswith(w)
    return a.endswith(w)


def evaluate_value(op: str, cell: Cell, wanted: Any = None) -> bool:
    """One comparison of a property or story cell. Binary operators need a
    usable value: an element with none matches no binary operator, not_equal
    and does_not_contain included."""
    if op == "has_value":
        return cell.usable
    if op == "has_no_value":
        return not cell.usable
    if op == "is_user_undefined":
        return cell.status == "userUndefined"
    if op == "is_not_user_undefined":
        return cell.status != "userUndefined"
    if op == "available":
        return cell.status != "notAvailable"
    if op == "not_available":
        return cell.status == "notAvailable"
    if not cell.usable:
        return False
    actual = cell.value
    if op == "equal":
        if isinstance(actual, list) and not isinstance(wanted, list):
            return any(_equal(x, wanted) for x in actual)
        return _equal(actual, wanted)
    if op == "not_equal":
        if isinstance(actual, list) and not isinstance(wanted, list):
            return not any(_equal(x, wanted) for x in actual)
        return not _equal(actual, wanted)
    if op in STRING_OPERATORS:
        return _string_op(op, actual, wanted)
    order = _order(actual, wanted)
    if order is None:
        return False
    return {"less": order < 0, "greater": order > 0,
            "less_or_equal": order <= 0, "greater_or_equal": order >= 0}[op]


def evaluate_classification(op: str, cell: Cell, wanted: Any,
                            tree: ClassificationTree | None) -> bool:
    """cell.value is the element's item GUID in the system (or missing)."""
    if op in UNARY_OPERATORS:
        return evaluate_value(op, cell)
    if not cell.usable:
        return False
    guid = cell.value
    if tree is None:
        # No tree (a rule snapshot): only identity tests are possible, against
        # the GUID itself.
        if op == "equal":
            return _fold(guid) == _fold(wanted)
        if op == "not_equal":
            return _fold(guid) != _fold(wanted)
        return False
    target = tree.resolve(str(wanted))
    if target is None:
        return op in ("not_equal", "is_not_in_branch_of", "is_not_direct_child_of")
    if op == "equal":
        return guid == target
    if op == "not_equal":
        return guid != target
    in_branch = guid == target or target in tree.ancestors(guid)
    if op == "is_in_branch_of":
        return in_branch
    if op == "is_not_in_branch_of":
        return not in_branch
    direct = tree.parent(guid) == target
    return direct if op == "is_direct_child_of" else not direct


CellProvider = Callable[[Comparison], Cell]
TreeProvider = Callable[[str], "ClassificationTree | None"]


def comparison_matches(cmp: Comparison, cell: Cell,
                       tree: ClassificationTree | None = None) -> bool:
    if cmp.kind == "classification":
        return evaluate_classification(cmp.operator, cell, cmp.value, tree)
    return evaluate_value(cmp.operator, cell, cmp.value)


def group_matches(group: CriteriaGroup, element_type: str, cells: CellProvider,
                  trees: TreeProvider | None = None) -> bool:
    """Does one element satisfy the group? `cells` returns the element's Cell
    for a comparison; `trees` returns the ClassificationTree for a system."""
    if not group.type_matches(element_type):
        return False
    if not group.comparisons:
        return True
    results = (
        comparison_matches(c, cells(c), trees(c.system) if trees and c.kind == "classification" else None)
        for c in group.comparisons
    )
    return any(results) if group.logical_operator == "or" else all(results)


def properties_referenced(groups: Iterable[CriteriaGroup]) -> list[str]:
    """Property addresses (kind == 'property') in first-seen order."""
    seen: dict[str, None] = {}
    for g in groups:
        for c in g.comparisons:
            if c.kind == "property":
                seen.setdefault(c.property)
    return list(seen)


def systems_referenced(groups: Iterable[CriteriaGroup]) -> list[str]:
    seen: dict[str, None] = {}
    for g in groups:
        for c in g.comparisons:
            if c.kind == "classification":
                seen.setdefault(c.system)
    return list(seen)


def needs_story(groups: Iterable[CriteriaGroup]) -> bool:
    return any(c.kind == "story" for g in groups for c in g.comparisons)
