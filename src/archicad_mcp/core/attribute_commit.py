"""edit_attributes commit: send the planned changes grouped by command, then
read the attributes back and report what Archicad actually did.

Order: per type in WRITE_ORDER (a type before the types that refer to it),
creates before modifies; then property values; deletes last, so nothing is
deleted that another change still names. A reference to an attribute created
in this call is resolved just before its batch, by name from a fresh read:
Tapir's returned GUID is not trusted (its source returns whatever is in the
header after Create).
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.core.attribute_edit import (
    Plan,
    _Context,
    _diff,
    _integers,
    _item_schema,
    _project,
    _to_tapir,
    renamed_references,
)
from archicad_mcp.core.attribute_types import WRITE_ORDER
from archicad_mcp.core.attributes import named_references, read_records
from archicad_mcp.core.definition_edit import group_by_error
from archicad_mcp.core.element_data import error_fields
from archicad_mcp.connection import ArchicadUnavailableError

NOT_WRITTEN = ("not written: the property is not available for this attribute, or the "
               "attribute is gone (Archicad answered neither success nor an error)")


def _label(p: Plan) -> str:
    return f"{p.type_name}/{p.target}"


def _batches(plans: list[Plan]) -> list[tuple[str, list[Plan], bool | None]]:
    batches: list[tuple[str, list[Plan], bool | None]] = []
    for type_name in WRITE_ORDER:
        for action, overwrite in (("create", False), ("modify", True)):
            batch = [p for p in plans if p.type_name == type_name and p.action == action
                     and p.send_fields]
            if batch:
                batches.append(("fields", batch, overwrite))
    props = [p for p in plans if p.props]
    if props:
        batches.append(("properties", props, None))
    deletes = [p for p in plans if p.action == "delete"]
    if deletes:
        batches.append(("delete", deletes, None))
    return batches


class _Run:
    def __init__(self, ctx: _Context, plans: list[Plan]):
        self.ctx = ctx
        self.plans = plans
        self.fields_ok: list[Plan] = []
        self.props_ok: dict[int, list[dict]] = {}
        self.deletes_ok: list[Plan] = []
        self.failed: list[dict] = []
        self.stopped: list[dict] = []
        self.error: Exception | None = None

    def refused(self, plans: list[Plan], exc: Exception) -> None:
        self.error = exc
        message = error_fields(exc)["message"]
        self.failed += [{"target": _label(p), "message": message} for p in plans]

    def send(self, kind: str, batch: list[Plan], overwrite: bool | None) -> None:
        if self.error is not None:
            self.stopped += [p.head() for p in batch]
            return
        if kind == "fields":
            self._send_fields(batch, overwrite)
        elif kind == "properties":
            self._send_properties(batch)
        else:
            self._send_deletes(batch)

    def _send_fields(self, batch: list[Plan], overwrite: bool) -> None:
        attr = batch[0].attr
        items, ready = [], []
        for p in batch:
            item, errors = _to_tapir(self.ctx, p, pending_ok=False)
            if errors:
                self.failed.append({"target": _label(p), "message": errors[0]})
                continue
            items.append(_integers(item, _item_schema(attr)))
            ready.append(p)
        if not items:
            return
        try:
            response = self.ctx.conn.tapir(attr.create, {attr.array_key: items,
                                                         "overwriteExisting": overwrite})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            self.refused(ready, exc)
            return
        answers = response.get("attributeIds", [])
        for i, p in enumerate(ready):
            answer = answers[i] if i < len(answers) else {}
            if isinstance(answer, dict) and answer and "error" not in answer:
                self.fields_ok.append(p)
            else:
                error = answer.get("error", {}) if isinstance(answer, dict) else {}
                self.failed.append({"target": _label(p),
                                    "message": error.get("message", "no answer for this item")})
        # Later batches may name what this one created.
        self.ctx.names.forget(attr.name)

    def _send_properties(self, batch: list[Plan]) -> None:
        rows = [(p, w) for p in batch for w in p.props]
        items = [{"attributeId": {"guid": p.guid}, "propertyId": {"guid": w["guid"]},
                  "propertyValue": {"value": w["value"]}} for p, w in rows]
        try:
            response = self.ctx.conn.tapir("SetPropertyValuesOfAttributes",
                                           {"attributePropertyValues": items})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            self.refused(batch, exc)
            return
        results = response.get("executionResults", [])
        for i, (p, w) in enumerate(rows):
            answer = results[i] if i < len(results) else {}
            target = f"{_label(p)} {w['address']}"
            if isinstance(answer, dict) and answer.get("success"):
                self.props_ok.setdefault(id(p), []).append(w)
            elif isinstance(answer, dict) and answer.get("error"):
                self.failed.append({"target": target,
                                    "message": answer["error"].get("message", "refused")})
            else:
                self.failed.append({"target": target, "message": NOT_WRITTEN})

    def _send_deletes(self, batch: list[Plan]) -> None:
        items = [{"attributeType": p.type_name,
                  "attributeId": {"attributeId": {"guid": p.guid}}} for p in batch]
        try:
            response = self.ctx.conn.tapir("DeleteAttributes", {"attributesToDelete": items})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            self.refused(batch, exc)
            return
        results = response.get("executionResults", [])
        for i, p in enumerate(batch):
            answer = results[i] if i < len(results) else {}
            if isinstance(answer, dict) and answer.get("success"):
                self.deletes_ok.append(p)
            else:
                error = answer.get("error", {}) if isinstance(answer, dict) else {}
                self.failed.append({"target": _label(p),
                                    "message": error.get("message", "no answer for this item")})


def _verify_shape(p: Plan, rename_to: dict) -> dict:
    """What the read-back is compared with: the merged values of the fields asked
    for (a create also its name), a profile's skinOverrides as asked, references
    to attributes renamed in this call by their new name."""
    shape = {"name": p.target, **p.expected} if p.action == "create" else dict(p.expected)
    if "skinOverrides" in p.asked:
        shape["skinOverrides"] = p.asked["skinOverrides"]
    return renamed_references(shape, p.type_name, rename_to)


def _now_shape(named: dict, shape: dict) -> dict:
    now = _project(named, {k: v for k, v in shape.items() if k != "skinOverrides"})
    if "skinOverrides" in shape:
        skins = {s.get("skinId"): s for s in named.get("skins", []) if isinstance(s, dict)}
        now["skinOverrides"] = [
            {"skinId": o.get("skinId"),
             **{k: skins.get(o.get("skinId"), {}).get(k) for k in o if k != "skinId"}}
            for o in shape["skinOverrides"] if isinstance(o, dict)]
    return now


def _read_back(run: _Run) -> tuple[list[dict], list[dict]]:
    ctx = run.ctx
    for type_name in {p.type_name for p in run.fields_ok + run.deletes_ok}:
        ctx.names.forget(type_name)
    applied: dict[int, dict] = {}
    not_applied: list[dict] = []
    for p in run.fields_ok:
        header = (ctx.names.by_name(p.type_name, p.target) if p.action == "create"
                  else ctx.names.by_guid(p.type_name, p.guid))
        entry = applied.setdefault(id(p), {**p.head(), "now": {}})
        shape = _verify_shape(p, ctx.rename_to)
        record = read_records(ctx.conn, p.attr, [header["guid"]])[0] if header else None
        if record is None:
            not_applied.append({"type": p.type_name, "target": p.target, "field": "(read)",
                                "sent": "written", "read": "not found afterwards"})
            continue
        now = _now_shape(named_references(record, p.type_name, ctx.names), shape)
        entry["now"].update(now)
        for path, (read, sent) in _diff(now, shape).items():
            not_applied.append({"type": p.type_name, "target": p.target, "field": path,
                                "sent": sent, "read": read})
    for p in [p for p in run.plans if id(p) in run.props_ok]:
        written = run.props_ok[id(p)]
        answer = ctx.conn.tapir("GetPropertyValuesOfAttributes", {
            "attributeIds": [{"attributeId": {"guid": p.guid}}],
            "properties": [{"propertyId": {"guid": w["guid"]}} for w in written]})
        row = (answer.get("propertyValuesForAttributes") or [{}])[0]
        cells = row.get("propertyValues", []) if isinstance(row, dict) else []
        entry = applied.setdefault(id(p), {**p.head(), "now": {}})
        values = entry["now"].setdefault("properties", {})
        for i, w in enumerate(written):
            cell = cells[i] if i < len(cells) else {}
            value = (cell.get("propertyValue") or {}).get("value") \
                if isinstance(cell, dict) else None
            values[w["address"]] = value
            if w["compare"] and value != w["value"]:
                not_applied.append({"type": p.type_name, "target": p.target,
                                    "field": f"properties.{w['address']}",
                                    "sent": w["value"], "read": value})
    for p in run.deletes_ok:
        if ctx.names.by_guid(p.type_name, p.guid) is None:
            applied[id(p)] = p.head()
        else:
            not_applied.append({"type": p.type_name, "target": p.target, "field": "deleted",
                                "sent": True, "read": False})
    return list(applied.values()), not_applied


def commit(ctx: _Context, plans: list[Plan], result: dict) -> None:
    run = _Run(ctx, plans)
    for kind, batch, overwrite in _batches(plans):
        run.send(kind, batch, overwrite)
    applied, not_applied = _read_back(run)
    result["applied"] = applied
    if not_applied:
        result["not_applied"] = not_applied
    if run.failed:
        groups = group_by_error(run.failed)
        problems = [e["problem"] for e in result.get("teamwork", []) if e.get("problem")]
        if problems:
            for g in groups:
                g["hint"] = "Teamwork: " + "; ".join(problems)
        result["failed"] = groups
    if run.error is not None:
        result["error"] = error_fields(run.error)
    if run.stopped:
        result["stopped"] = run.stopped
