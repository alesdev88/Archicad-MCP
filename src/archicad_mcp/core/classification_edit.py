"""edit_classifications: in-place edits of classification systems and items.

Items keep their GUID through a code or name change, so elements classified with
them and properties available for them stay attached. Reparenting is not
offered: the API item has no parent field, and delete plus recreate would
detach every element.
"""
from __future__ import annotations

import re
from pathlib import Path

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, Definitions, PlannedEdit, editing_unavailable, group_by_error,
    split_results)
from archicad_mcp.core.definition_xml import parse_classification_xml, parse_property_xml
from archicad_mcp.core.element_data import error_fields

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYSTEM_FIELDS = {"name": "name", "description": "description", "source": "source",
                  "version": "version", "date": "date"}
_ITEM_FIELDS = {"code": "id", "name": "name", "description": "description"}


def _plan_system(change: dict, index: ClassificationIndex) -> PlannedEdit:
    ref = str(change["system"])
    system = index.systems.get(ref) or index.system_of_guid(ref)
    if system is None and ref in index.ambiguous:
        return PlannedEdit(ref, {}, errors=[
            f"'{ref}' names {len(index.ambiguous[ref])} systems "
            f"({', '.join(index.ambiguous[ref])}); name one of those, or use its GUID"])
    if system is None:
        return PlannedEdit(ref, {}, errors=[f"no classification system '{ref}'; "
                                            f"systems here: {sorted(index.systems)}"])
    plan = PlannedEdit(system.label, {"classificationSystemId": {"guid": system.guid}})
    unknown = sorted(set(change) - {"system", *_SYSTEM_FIELDS})
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(_SYSTEM_FIELDS)}")
        return plan
    for key, wire in _SYSTEM_FIELDS.items():
        if key in change and change[key] != getattr(system, key):
            plan.payload[wire] = change[key]
            plan.changes[key] = [getattr(system, key), change[key]]
    if "date" in change and not _DATE.match(str(change["date"])):
        plan.errors.append("date must be YYYY-MM-DD")
    new_name = change.get("name", system.name)
    new_version = change.get("version", system.version)
    if (new_name, new_version) != (system.name, system.version) and any(
            (s.name, s.version) == (new_name, new_version) for s in index.by_guid.values()):
        plan.errors.append(f"a classification system '{new_name}' already exists"
                           + (f" in version '{new_version}'" if new_version else ""))
    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


def _plan_item(change: dict, index: ClassificationIndex) -> PlannedEdit:
    ref = str(change["item"])
    if ref.endswith("/*"):
        return PlannedEdit(ref, {}, errors=["an item edit names one item; drop the '/*'"])
    guids, err = index.resolve(ref)
    if err:
        return PlannedEdit(ref, {}, errors=[err])
    item = index.items[guids[0]]
    plan = PlannedEdit(index.label(item.guid), {"classificationItemId": {"guid": item.guid}})
    unknown = sorted(set(change) - {"item", *_ITEM_FIELDS})
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(_ITEM_FIELDS)}")
        return plan
    for key, wire in _ITEM_FIELDS.items():
        current = item.code if key == "code" else getattr(item, key)
        if key in change and change[key] != current:
            plan.payload[wire] = change[key]
            plan.changes[key] = [current, change[key]]
    if "code" in plan.changes:
        clash = [g for g in index.siblings(item.guid) if index.items[g].code == change["code"]]
        if clash:
            plan.errors.append(f"code '{change['code']}' is already used by a sibling "
                               f"({index.label(clash[0])})")
    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


def edit_classifications(conn: ArchicadConnection, changes: list[dict],
                         dry_run: bool = True) -> dict:
    gate = editing_unavailable(conn)
    if gate:
        return gate
    index = ClassificationIndex.load(conn)
    plans: list[tuple[str, PlannedEdit]] = []
    for change in changes:
        if ("system" in change) == ("item" in change):
            plans.append(("?", PlannedEdit(str(change), {}, errors=[
                "give exactly one of 'system' or 'item'"])))
        elif "system" in change:
            plans.append(("system", _plan_system(change, index)))
        else:
            plans.append(("item", _plan_item(change, index)))

    result: dict = {"dry_run": dry_run,
                    "planned": [p.entry() for _, p in plans if not p.errors]}
    skipped = [p.entry() for _, p in plans if p.errors]
    if skipped:
        result["skipped"] = skipped
    batches = [
        ("UpdateClassificationSystems", "classificationSystems",
         [p for k, p in plans if k == "system" and not p.errors and len(p.payload) > 1]),
        ("UpdateClassificationItems", "classificationItems",
         [p for k, p in plans if k == "item" and not p.errors and len(p.payload) > 1]),
    ]
    if dry_run or not any(b for _, _, b in batches):
        return result

    applied, failed = [], []
    for command, key, batch in batches:
        if not batch:
            continue
        try:
            response = conn.tapir(command, {key: [p.payload for p in batch]})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            result["error"] = error_fields(exc)
            break
        ok, bad = split_results([p.target for p in batch], response)
        applied += ok
        failed += bad
    result["applied"] = applied
    if failed:
        result["failed"] = group_by_error(failed)
    return result


# ---------- import_definitions ----------

POLICIES = {"property": ["append", "replace", "skip"],
            "classification": ["merge", "replace", "skip"]}
ITEM_POLICIES = ["replace", "skip"]
MAX_XML_BYTES = 20 * 1024 * 1024
POLICY_EFFECT = {
    ("property", "append"): "colliding properties are imported under a new, unused name; "
                            "existing ones stay",
    # Verified live on AC 29 (2026-09-25): replace keeps the property GUID.
    ("property", "replace"): "colliding properties are updated in place from the file; they "
                             "keep their GUID, so element values survive",
    ("property", "skip"): "colliding properties stay as they are; the imported ones are dropped",
    ("classification", "merge"): "colliding systems are merged: new items are added, and "
                                 "colliding items follow item_conflict",
    ("classification", "replace"): "colliding systems are replaced by the imported ones "
                                   "(whether item GUIDs survive is not verified; merge is safer)",
    ("classification", "skip"): "colliding systems stay as they are; the imported ones are dropped",
}
IMPORT_CAP = 50


def _capped_names(names: list[str]) -> list[str]:
    if len(names) <= IMPORT_CAP:
        return names
    return names[:IMPORT_CAP] + [f"... and {len(names) - IMPORT_CAP} more"]


def import_definitions(conn: ArchicadConnection, kind: str, xml_path: str, conflict: str,
                       item_conflict: str = "skip", dry_run: bool = True) -> dict:
    if kind not in POLICIES:
        return {"error": "kind is 'property' or 'classification'"}
    if conflict not in POLICIES[kind]:
        return {"error": f"conflict for {kind} imports is one of {POLICIES[kind]}"}
    if kind == "classification" and item_conflict not in ITEM_POLICIES:
        return {"error": f"item_conflict is one of {ITEM_POLICIES}"}
    path = Path(xml_path).expanduser()
    try:
        if path.stat().st_size > MAX_XML_BYTES:
            return {"error": f"{path} is larger than 20 MB"}
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": f"cannot read {path}: {exc.strerror or exc}"}
    gate = editing_unavailable(conn)
    if gate:
        return gate

    try:
        if kind == "property":
            defs = Definitions.load(conn)
            names = [f"{g}/{n}" for g, n in parse_property_xml(text)]
            existing = set(defs.by_address)
            result: dict = {"dry_run": dry_run, "kind": kind,
                            "new": _capped_names([n for n in names if n not in existing]),
                            "collisions": _capped_names([n for n in names if n in existing])}
        else:
            index = ClassificationIndex.load(conn)
            result = {"dry_run": dry_run, "kind": kind,
                      **_classification_preview(parse_classification_xml(text), index, conflict)}
    except ValueError as exc:
        return {"error": str(exc)}
    result["policy"] = POLICY_EFFECT[(kind, conflict)]
    if dry_run:
        return result

    if kind == "property":
        command, params = "ImportPropertiesXml", {"xml": text, "conflictPolicy": conflict}
    else:
        command, params = "ImportClassificationsXml", {
            "xml": text, "systemConflictPolicy": conflict, "itemConflictPolicy": item_conflict}
    try:
        response = conn.tapir(command, params)
    except (APIErrorBase, ArchicadUnavailableError) as exc:
        result["error"] = error_fields(exc)
        return result
    outcome = response.get("executionResult", {})
    if not outcome.get("success"):
        result["error"] = outcome.get("error", {}).get("message", "import refused")
        return result
    created = [c["guid"] for c in response.get("created", [])]
    removed = [c["guid"] for c in response.get("removed", [])]
    # Created things exist only after the import, removed ones only before it.
    if kind == "property":
        after = Definitions.load(conn)
        result["created"] = _capped_names([_property_label(after, g) for g in created])
        result["removed"] = _capped_names([_property_label(defs, g) for g in removed])
    else:
        after_index = ClassificationIndex.load(conn)
        result["created"] = _capped_names([_class_label(after_index, g) for g in created])
        result["removed"] = _capped_names([_class_label(index, g) for g in removed])
    return result


def _property_label(defs: Definitions, guid: str) -> str:
    return defs.by_guid[guid].address if guid in defs.by_guid else guid


def _class_label(index: ClassificationIndex, guid: str) -> str:
    if guid in index.by_guid:
        return index.by_guid[guid].label
    return index.label(guid)


def _classification_preview(systems: list[tuple[str, list[str]]],
                            index: ClassificationIndex, conflict: str) -> dict:
    """What the import would do, per the system policy. skip and replace act on
    whole systems, merge on items, so an item-level list alone misleads."""
    existing_names = {s.name for s in index.by_guid.values()}
    existing_codes: dict[str, set[str]] = {}
    for item in index.items.values():
        existing_codes.setdefault(index.systems[item.system].name, set()).add(item.code)
    new_systems = [n for n, _ in systems if n not in existing_names]
    colliding = [n for n, _ in systems if n in existing_names]
    new, collisions, skipped, dropped = [], [], [], []
    for name, codes in systems:
        have = existing_codes.get(name, set())
        labels = [f"{name}/{c}" for c in codes]
        if name not in existing_names:
            new += labels
        elif conflict == "skip":
            skipped += labels
        else:
            new += [f"{name}/{c}" for c in codes if c not in have]
            collisions += [f"{name}/{c}" for c in codes if c in have]
            if conflict == "replace":
                dropped += [f"{name}/{c}" for c in sorted(have - set(codes))]
    out = {"systems": {"new": new_systems, "colliding": colliding},
           "new": _capped_names(new), "collisions": _capped_names(collisions)}
    if skipped:
        out["skipped"] = _capped_names(skipped)
    if dropped:
        out["removed_if_replaced"] = _capped_names(dropped)
    return out
