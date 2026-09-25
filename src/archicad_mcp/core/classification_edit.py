"""edit_classifications: in-place edits of classification systems and items.

Items keep their GUID through a code or name change, so elements classified with
them and properties available for them stay attached. Reparenting is not
offered: the API item has no parent field, and delete plus recreate would
detach every element.
"""
from __future__ import annotations

import re

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, PlannedEdit, editing_unavailable, group_by_error, split_results)
from archicad_mcp.core.element_data import error_fields

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYSTEM_FIELDS = {"name": "name", "description": "description", "source": "source",
                  "version": "version", "date": "date"}
_ITEM_FIELDS = {"code": "id", "name": "name", "description": "description"}


def _plan_system(change: dict, index: ClassificationIndex) -> PlannedEdit:
    ref = str(change["system"])
    system = index.systems.get(ref) or index.system_of_guid(ref)
    if system is None:
        return PlannedEdit(ref, {}, errors=[f"no classification system '{ref}'; "
                                            f"systems here: {sorted(index.systems)}"])
    plan = PlannedEdit(system.name, {"classificationSystemId": {"guid": system.guid}})
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
    if new_name != system.name and new_name in index.systems:
        plan.errors.append(f"a classification system '{new_name}' already exists")
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
