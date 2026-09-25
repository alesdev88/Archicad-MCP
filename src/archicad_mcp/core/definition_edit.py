"""Editing property and classification DEFINITIONS in place.

Definition reads and Tapir Update* commands only. Nothing here calls
GetPropertyValuesOfElements, so none of it can trigger the Archicad 29
property-read crash (docs/known-issues.md).

Every edit keeps GUIDs: the Tapir commands read the definition, patch only the
fields sent and call ACAPI_*_Change*. That is what keeps element values,
classifications and availability links attached through a rename.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import value_fit

EDIT_MARKER = "UpdateClassificationItems"
FAILURE_SAMPLE = 5

NEEDS_BUILD = (
    "Editing definitions needs a Tapir add-on that has UpdateClassificationItems: "
    "the property-classification-editing build, or the upstream Tapir release that "
    "ships it. The add-on installed in this Archicad does not have it, so nothing "
    "was sent. Older Tapir versions accept UpdatePropertyDefinitions but ignore the "
    "new fields, which is why this is checked first.")


def editing_unavailable(conn: ArchicadConnection) -> dict | None:
    """An error result when the running Tapir lacks the editing commands."""
    if conn.tapir_command_available(EDIT_MARKER):
        return None
    return {"error": NEEDS_BUILD}


def group_by_error(failed: list[dict]) -> list[dict]:
    """Refusals grouped by message, largest group first, a few targets each."""
    groups: dict[str, dict] = {}
    for f in failed:
        g = groups.setdefault(f["message"], {"message": f["message"], "count": 0, "sample": []})
        g["count"] += 1
        if len(g["sample"]) < FAILURE_SAMPLE:
            g["sample"].append(f["target"])
    return sorted(groups.values(), key=lambda g: -g["count"])


def split_results(targets: list[str], response: dict) -> tuple[list[str], list[dict]]:
    """(applied targets, failed [{target, message}]) from an executionResults reply."""
    applied, failed = [], []
    for target, r in zip(targets, response.get("executionResults", [])):
        if r.get("success"):
            applied.append(target)
        else:
            failed.append({"target": target,
                           "message": r.get("error", {}).get("message", "refused")})
    return applied, failed


# ---------- classifications ----------

@dataclass
class ClassItem:
    guid: str
    code: str
    name: str
    description: str
    system: str
    parent: str | None
    children: list[str] = field(default_factory=list)


@dataclass
class ClassSystem:
    guid: str
    name: str
    description: str
    source: str
    version: str
    date: str
    roots: list[str] = field(default_factory=list)


class ClassificationIndex:
    """Every classification system and item of the open project, by name and code."""

    def __init__(self) -> None:
        self.systems: dict[str, ClassSystem] = {}
        self.items: dict[str, ClassItem] = {}

    @classmethod
    def load(cls, conn: ArchicadConnection) -> "ClassificationIndex":
        index = cls()
        response = conn.official("API.GetAllClassificationSystems")
        for s in response.get("classificationSystems", []):
            system = ClassSystem(
                guid=s["classificationSystemId"]["guid"], name=s.get("name", ""),
                description=s.get("description", ""), source=s.get("source", ""),
                version=s.get("version", ""), date=s.get("date", ""))
            index.systems[system.name] = system
            tree = conn.official("API.GetAllClassificationsInSystem",
                                 {"classificationSystemId": s["classificationSystemId"]})
            system.roots = index._walk(tree.get("classificationItems", []), system.name, None)
        return index

    def _walk(self, wrappers: list[dict], system: str, parent: str | None) -> list[str]:
        guids = []
        for wrapper in wrappers:
            item = wrapper.get("classificationItem", wrapper)
            guid = item.get("classificationItemId", {}).get("guid")
            if not guid:
                continue
            node = ClassItem(guid, item.get("id", ""), item.get("name", ""),
                             item.get("description", ""), system, parent)
            self.items[guid] = node
            node.children = self._walk(item.get("children", []), system, guid)
            guids.append(guid)
        return guids

    def system_of_guid(self, guid: str) -> ClassSystem | None:
        return next((s for s in self.systems.values() if s.guid == guid), None)

    def label(self, guid: str) -> str:
        item = self.items.get(guid)
        return f"{item.system}/{item.code}" if item else guid

    def descendants(self, guid: str) -> list[str]:
        out: list[str] = []
        for child in self.items[guid].children:
            out.append(child)
            out.extend(self.descendants(child))
        return out

    def siblings(self, guid: str) -> list[str]:
        item = self.items[guid]
        pool = (self.items[item.parent].children if item.parent
                else self.systems[item.system].roots)
        return [g for g in pool if g != guid]

    def _by_code(self, ref: str) -> tuple[str | None, str | None]:
        # Longest system name first: "ELEA 2/40" must not match system "ELEA".
        for name in sorted(self.systems, key=len, reverse=True):
            if ref.startswith(name + "/"):
                code = ref[len(name) + 1:]
                matches = [g for g, i in self.items.items()
                           if i.system == name and i.code == code]
                if len(matches) == 1:
                    return matches[0], None
                if matches:
                    return None, f"'{ref}' matches {len(matches)} items; address it by GUID"
                return None, f"no classification item '{ref}' (code '{code}' in system '{name}')"
        known = ", ".join(sorted(self.systems)) or "none"
        return None, f"no classification item '{ref}': write System/Code; systems here: {known}"

    def resolve(self, address: str) -> tuple[list[str], str | None]:
        """'System/Code' -> [guid]; 'System/Code/*' -> that item and all below it;
        an item GUID -> [guid]."""
        branch = address.endswith("/*")
        ref = address[:-2] if branch else address
        if ref in self.items:
            guid, err = ref, None
        else:
            guid, err = self._by_code(ref)
        if guid is None:
            return [], err
        return ([guid, *self.descendants(guid)] if branch else [guid]), None


# ---------- property definitions ----------

_BUILTIN = frozenset({"StaticBuiltIn", "DynamicBuiltIn"})
_ENUM = frozenset({"SingleChoiceEnumeration", "MultipleChoiceEnumeration"})
_SCALAR = {("String", "Default"): "string", ("Integer", "Default"): "integer",
           ("Boolean", "Default"): "boolean", ("Real", "Default"): "number",
           ("Real", "Length"): "length", ("Real", "Area"): "area",
           ("Real", "Volume"): "volume", ("Real", "Angle"): "angle"}
FIELDS = frozenset({"property", "name", "description", "group", "default",
                    "expressions", "availability", "enum"})
LIST_CAP = 20


@dataclass
class PropDef:
    guid: str
    group: str
    name: str
    builtin: bool
    collection: str
    value_type: str
    measure: str
    expressions: list[str] | None
    enum: list[tuple[str, str]]
    description: str
    group_guid: str | None
    default: str | None
    availability: list[str]

    @property
    def address(self) -> str:
        return f"{self.group}/{self.name}"

    @property
    def type_key(self) -> str | None:
        """The Tapir type name (CreatePropertyDefinitions `type`) of this definition."""
        if self.collection == "SingleChoiceEnumeration":
            return "singleEnum"
        if self.collection == "MultipleChoiceEnumeration":
            return "multiEnum"
        scalar = _SCALAR.get((self.value_type, self.measure))
        if scalar and self.collection == "List":
            return scalar + "List"
        return scalar if self.collection == "Single" else None

    def default_state(self):
        return {"expressions": self.expressions} if self.expressions is not None else self.default


class Definitions:
    """Every property definition, by address ("Group/Name", custom only) and guid."""

    def __init__(self, props: list[PropDef], groups: dict[str, str]):
        self.by_guid = {p.guid: p for p in props}
        self.by_address = {p.address: p for p in props if not p.builtin}
        self.groups = groups

    @classmethod
    def load(cls, conn: ArchicadConnection) -> "Definitions":
        raw = conn.tapir("GetAllProperties")
        props = []
        for p in raw.get("properties", []):
            props.append(PropDef(
                guid=p["propertyId"]["guid"], group=p.get("propertyGroupName", ""),
                name=p.get("propertyName", ""), builtin=p.get("propertyType") in _BUILTIN,
                collection=p.get("propertyCollectionType", ""),
                value_type=p.get("propertyValueType", ""),
                measure=p.get("propertyMeasureType", ""),
                expressions=p.get("expressions") if p.get("isExpressionBased") else None,
                enum=[(e["enumValue"].get("guid", ""), e["enumValue"].get("displayValue", ""))
                      for e in p.get("possibleEnumValues", [])],
                description=p.get("propertyDescription", ""),
                group_guid=p.get("propertyGroupId", {}).get("guid"),
                default=p.get("defaultValueDisplay"),
                availability=[a["classificationItemId"]["guid"]
                              for a in p.get("availability", [])]))
        groups = {g["name"]: g["propertyGroupId"]["guid"]
                  for g in raw.get("propertyGroups", []) if g.get("isCustom")}
        return cls(props, groups)

    def resolve(self, ref: str) -> tuple[PropDef | None, str | None]:
        # Whole-string match: group names may contain "/", so never split.
        p = self.by_address.get(ref) or self.by_guid.get(ref)
        if p is None:
            return None, (f"no property '{ref}'; address custom properties as Group/Name "
                          "(search_definitions finds the exact address) or by GUID")
        return p, None


@dataclass
class PlannedEdit:
    target: str
    payload: dict
    changes: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def entry(self) -> dict:
        out = {"target": self.target, "changes": self.changes}
        if self.warnings:
            out["warnings"] = self.warnings
        if self.errors:
            out["errors"] = self.errors
        return out


def _capped(labels: list[str]) -> list[str]:
    labels = sorted(labels)
    if len(labels) > LIST_CAP:
        return labels[:LIST_CAP] + [f"... and {len(labels) - LIST_CAP} more"]
    return labels


def _default_payload(p: PropDef, value, options: list[str]) -> tuple[dict | None, str | None]:
    if value is None:
        return {"basicDefaultValue": {"status": "userUndefined"}}, None
    key = p.type_key
    if key == "singleEnum":
        if options.count(value) != 1:
            return None, f"default '{value}' is not exactly one option of {options}"
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": {
            "type": "displayValue", "displayValue": value}}}, None
    if key == "multiEnum":
        values = value if isinstance(value, list) else [value]
        bad = [v for v in values if options.count(v) != 1]
        if bad:
            return None, f"default options {bad} are not exactly one option each of {options}"
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": [
            {"enumValueId": {"type": "displayValue", "displayValue": v}} for v in values]}}, None
    if key is None:
        return None, f"cannot set a default on a {p.collection} {p.value_type} property"
    if key.endswith("List"):
        if not isinstance(value, list):
            return None, f"'{key}' default takes a list"
        sent = []
        for v in value:
            fitted, err = value_fit(key[:-4], v)
            if err:
                return None, err
            sent.append(fitted)
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": sent}}, None
    fitted, err = value_fit(key, value)
    if err:
        return None, err
    return {"basicDefaultValue": {"status": "normal", "type": key, "value": fitted}}, None


def _plan_availability(spec: dict, p: PropDef, index: ClassificationIndex,
                       plan: PlannedEdit) -> None:
    if "set" in spec and ("add" in spec or "remove" in spec):
        plan.errors.append("availability takes either set, or add and/or remove")
        return

    def expand(addresses: list[str]) -> list[str]:
        out: list[str] = []
        for a in addresses:
            guids, err = index.resolve(a)
            if err:
                plan.errors.append(err)
            out.extend(g for g in guids if g not in out)
        return out

    def ids(guids: list[str]) -> list[dict]:
        return [{"classificationItemId": {"guid": g}} for g in guids]

    current = set(p.availability)
    if "set" in spec:
        wanted = expand(spec["set"])
        new = set(wanted)
        plan.payload["availability"] = {"set": ids(wanted)}
    else:
        add, remove = expand(spec.get("add", [])), expand(spec.get("remove", []))
        new = (current | set(add)) - set(remove)
        payload = {}
        if add:
            payload["add"] = ids(add)
        if remove:
            payload["remove"] = ids(remove)
        plan.payload["availability"] = payload
    added, removed = new - current, current - new
    if added or removed:
        plan.changes["availability"] = {"added": _capped([index.label(g) for g in added]),
                                        "removed": _capped([index.label(g) for g in removed])}
    if removed:
        plan.warnings.append(
            f"no longer available for {len(removed)} classification item(s): values on "
            "elements with those classifications become not applicable")


def plan_property_change(change: dict, defs: Definitions,
                         index: ClassificationIndex | None) -> PlannedEdit:
    ref = str(change.get("property", ""))
    p, err = defs.resolve(ref)
    if p is None:
        return PlannedEdit(ref, {}, errors=[err])
    plan = PlannedEdit(p.address if not p.builtin else ref, {"propertyId": {"guid": p.guid}})
    if p.builtin:
        plan.errors.append("built-in properties cannot be edited")
        return plan
    unknown = sorted(set(change) - FIELDS)
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(FIELDS - {'property'})}")
        return plan

    new_name, new_group = p.name, p.group
    if "name" in change and change["name"] != p.name:
        new_name = change["name"]
        plan.payload["name"] = new_name
        plan.changes["name"] = [p.name, new_name]
    if "description" in change and change["description"] != p.description:
        plan.payload["description"] = change["description"]
        plan.changes["description"] = [p.description, change["description"]]
    if "group" in change and change["group"] != p.group:
        guid = defs.groups.get(change["group"])
        if guid is None:
            plan.errors.append(f"no custom property group '{change['group']}'; create it "
                               "first (Tapir CreatePropertyGroups)")
        else:
            new_group = change["group"]
            plan.payload["groupId"] = {"guid": guid}
            plan.changes["group"] = [p.group, new_group]
    target = f"{new_group}/{new_name}"
    if target != p.address and target in defs.by_address:
        plan.errors.append(f"'{target}' already exists")

    options = [d for _, d in p.enum]
    if "enum" in change:
        options = _plan_enum(change["enum"], p, plan)

    if "default" in change and "expressions" in change:
        plan.errors.append("send default or expressions, not both")
    elif "expressions" in change:
        exprs = change["expressions"]
        if not isinstance(exprs, list) or not exprs:
            plan.errors.append("expressions takes a non-empty list of expression strings")
        else:
            plan.payload["defaultValue"] = {"expressions": exprs}
            plan.changes["default"] = [p.default_state(), {"expressions": exprs}]
    elif "default" in change:
        payload, err = _default_payload(p, change["default"], options)
        if err:
            plan.errors.append(err)
        else:
            plan.payload["defaultValue"] = payload
            plan.changes["default"] = [p.default_state(), change["default"]]

    # The enum plan cannot know whether a new default follows, so the check that a
    # removed option is not left as the default lives here.
    removed = {r["enumValueId"]["guid"] for r in plan.payload.get("removeEnumValues", [])}
    removed_texts = {d for g, d in p.enum if g in removed}
    if p.default in removed_texts and "defaultValue" not in plan.payload:
        plan.errors.append(f"'{p.default}' is the default; send a new default in the same change")

    if "availability" in change:
        if index is None:
            plan.errors.append("availability needs the classification index")
        else:
            _plan_availability(change["availability"], p, index, plan)

    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


_ENUM_KEYS = frozenset({"rename", "remove", "add", "order"})


def _plan_enum(spec: dict, p: PropDef, plan: PlannedEdit) -> list[str]:
    """Plan enum option edits, applied in Tapir's order: rename, remove, add,
    order. Returns the option texts after the edit.

    Options are tracked as (guid, text) pairs so a rename or removal hits exactly
    the option named even when two options share a text; such an option is
    named by its GUID instead."""
    current = [d for _, d in p.enum]
    if p.collection not in _ENUM:
        plan.errors.append(f"'{p.address}' is not an enumeration property")
        return current
    unknown = sorted(set(spec) - _ENUM_KEYS)
    if unknown:
        plan.errors.append(f"unknown enum field(s) {unknown}; allowed: {sorted(_ENUM_KEYS)}")
        return current

    def guid_of(ref: str) -> str | None:
        if any(g == ref for g, _ in p.enum):
            return ref
        guids = [g for g, d in p.enum if d == ref]
        if len(guids) == 1:
            return guids[0]
        plan.errors.append(
            f"enum option '{ref}' does not exist; options: {current}" if not guids else
            f"enum option '{ref}' matches {len(guids)} options; address it by its GUID")
        return None

    renames: dict[str, str] = spec.get("rename", {})
    removes: list[str] = spec.get("remove", [])
    clash = sorted(set(renames) & set(removes))
    for ref in clash:
        plan.errors.append(f"enum option '{ref}' is both renamed and removed")
    if clash:
        return current

    after: list[list[str]] = [[g, d] for g, d in p.enum]
    if renames:
        out = []
        for ref, new in renames.items():
            guid = guid_of(ref)
            if guid:
                out.append({"enumValueId": {"guid": guid}, "displayValue": new})
                next(o for o in after if o[0] == guid)[1] = new
        plan.payload["renameEnumValues"] = out
    if removes:
        out, gone = [], []
        for ref in removes:
            guid = guid_of(ref)
            if guid:
                out.append({"enumValueId": {"guid": guid}})
                gone.append(guid)
        after = [o for o in after if o[0] not in gone]
        plan.payload["removeEnumValues"] = out
        plan.warnings.append(
            f"removing {removes}: elements holding these options lose that value. Not "
            "counted, because counting needs property value reads, which can crash Archicad")
    texts = [d for _, d in after]
    adds = [t for t in spec.get("add", []) if t not in texts]
    if adds:
        plan.payload["possibleEnumValues"] = [{"enumValue": {"displayValue": t}} for t in adds]
        texts.extend(adds)
    if "order" in spec:
        order = list(spec["order"])
        if sorted(order) != sorted(texts) or len(set(order)) != len(order):
            plan.errors.append(f"order must list every option exactly once: {texts}")
        else:
            plan.payload["enumOrder"] = order
            texts = order
    if texts != current:
        plan.changes["enum"] = [current, texts]
    return texts
