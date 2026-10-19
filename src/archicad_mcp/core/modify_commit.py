"""Send a modify_elements plan and report what Archicad actually did.

Order: SetDetailsOfElements (story, layer, draw order), then the
keep_elevation move, then the Modify commands, then a read of every sent
element. SetDetailsOfElements answers doors, windows and skylights that need
their host first (ElementCommands.cpp pre-scan, 1.5.9 to 1.7.1), so each batch
is sent in that order and results still match by position. Draw order steps
answer success whether or not they moved anything, and Archicad discards some
fields silently, so success is not trusted: the readback decides `modified`.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core import modify_fields as mf
from archicad_mcp.core.element_data import (
    FAILURE_SAMPLE,
    cap_list,
    error_fields,
    group_failures,
)
from archicad_mcp.core.modify import Plan, Planned, _grouped_warnings, _read_details
from archicad_mcp.core.mutate import _group
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK

_ERRORS = (APIErrorBase, ArchicadUnavailableError)


def setdetails_order(planned: list[Planned]) -> list[Planned]:
    """The order Tapir runs a SetDetailsOfElements batch in: doors, windows and
    skylights without a draw order of 1 to 7 first, each part in its own order."""
    def needs_host(p: Planned) -> bool:
        return p.type in mf.OPENING_TYPES and not (
            p.draw_order is not None and 1 <= p.draw_order <= 7)
    return [p for p in planned if needs_host(p)] + [p for p in planned if not needs_host(p)]


def _setdetails_item(p: Planned) -> dict:
    details = {}
    if p.story is not None:
        details["floorIndex"] = p.story
    if p.layer is not None:
        details["layerIndex"] = p.layer
    if p.draw_order is not None:
        details["drawIndex"] = p.draw_order
    return {"elementId": {"guid": p.guid}, "details": details}


def _modify_item(p: Planned) -> dict:
    if p.command == "ModifyMeshes":
        return {"elementId": {"guid": p.guid}, "meshData": p.item}
    return {"elementId": {"guid": p.guid}, **p.item}


class _Run:
    """The state of one commit: what was sent, what failed, what stopped it."""

    def __init__(self, conn: ArchicadConnection):
        self.conn = conn
        self.sent: dict[str, Planned] = {}
        self.failed: list[dict] = []
        self.failed_guids: set[str] = set()
        self.not_sent: dict[str, str] = {}
        self.stopped: APIErrorBase | ArchicadUnavailableError | None = None

    def send(self, command: str, batch: list[Planned], payload: dict) -> None:
        """One batch; per-element results in `batch` order."""
        for p in batch:
            self.sent[p.guid] = p
        try:
            response = self.conn.tapir(command, payload)
        except _ERRORS as exc:
            # Archicad may have acted before the error; the readback says.
            self.stopped = exc
            return
        results = (response or {}).get("executionResults", [])
        for i, p in enumerate(batch):
            outcome = results[i] if i < len(results) else None
            if isinstance(outcome, dict) and outcome.get("success") is True:
                continue
            error = (outcome.get("error") or {}) if isinstance(outcome, dict) else {}
            self.failed.append({
                "guid": p.guid, "command": command, "code": error.get("code"),
                "message": error.get("message",
                                     "Archicad returned no result for it")})
            self.failed_guids.add(p.guid)

    def batches(self, command: str, planned: list[Planned], build) -> None:
        for start in range(0, len(planned), PROPERTY_FETCH_CHUNK):
            if self.stopped is not None:
                self.skip(planned[start:], "not sent: the run stopped at an earlier "
                          f"command ({error_fields(self.stopped)['message']})")
                return
            self.send(command, planned[start:start + PROPERTY_FETCH_CHUNK],
                      build(planned[start:start + PROPERTY_FETCH_CHUNK]))

    def skip(self, planned: list[Planned], reason: str) -> None:
        for p in planned:
            self.not_sent.setdefault(p.guid, reason)


def _move_back(run: _Run, plan: Plan) -> None:
    """Undo the story change's height shift for keep_elevation elements, with
    Suspend Groups on around it so the move does not drag a group along."""
    movers = [p for p in plan.planned if p.keep_elevation and p.level_shift
              and p.guid in run.sent and p.guid not in run.failed_guids]
    if not movers:
        return
    if run.stopped is not None:
        run.skip(movers, "not moved back: the run stopped at an earlier command")
        return
    suspend = plan.suspend_groups is False and any(p.guid in plan.groups for p in movers)
    payload = {"elementsWithMoveVectors": [
        {"elementId": {"guid": p.guid}, "moveVector": {"x": 0, "y": 0, "z": -p.level_shift}}
        for p in movers]}
    try:
        if suspend:
            run.conn.tapir("SetSuspendGroupsMode", {"suspendGroups": True})
        try:
            run.send("MoveElements", movers, payload)
        finally:
            if suspend:
                run.conn.tapir("SetSuspendGroupsMode", {"suspendGroups": False})
    except _ERRORS as exc:
        run.stopped = run.stopped or exc


def _group_mates(conn: ArchicadConnection, plan: Plan) -> list[str]:
    groups = sorted(set(plan.groups.values()))
    if not groups or not conn.tapir_command_available("GetElementsOfGroups"):
        return []
    response = conn.tapir("GetElementsOfGroups",
                          {"groups": [{"groupId": {"guid": g}} for g in groups]})
    planned = {p.guid for p in plan.planned}
    mates = []
    for item in response.get("elementsOfGroups", []):
        for element in (item or {}).get("elements", []):
            guid = element.get("elementId", {}).get("guid")
            if guid and guid not in planned and guid not in mates:
                mates.append(guid)
    return mates


def _read_field(p: Planned, name: str, entry: dict):
    if name == "story":
        return entry.get("floorIndex")
    if name == "layer":
        return entry.get("layerIndex")
    if name == "draw_order":
        return entry.get("drawIndex")
    return mf.current_value(p.command, name, entry)


def _absolute_height(entry: dict, levels: dict[int, float]) -> float | None:
    details = entry.get("details") or {}
    if isinstance(details.get("zCoordinate"), (int, float)):
        return float(details["zCoordinate"])
    origin = details.get("origin")
    if isinstance(origin, dict) and isinstance(origin.get("z"), (int, float)):
        return float(origin["z"])
    if isinstance(details.get("level"), (int, float)) and entry.get("floorIndex") in levels:
        return levels[entry["floorIndex"]] + float(details["level"])
    return None


def _same(a, b) -> bool:
    return mf.values_match(a, b) and mf.values_match(b, a)


def commit(conn: ArchicadConnection, plan: Plan) -> dict:
    run = _Run(conn)
    mates = _group_mates(conn, plan)
    mates_before = _read_details(conn, mates) if mates else {}

    common = [p for p in plan.planned
              if p.story is not None or p.layer is not None or p.draw_order is not None]
    run.batches("SetDetailsOfElements", setdetails_order(common),
                lambda batch: {"elementsWithDetails": [_setdetails_item(p) for p in batch]})
    _move_back(run, plan)
    for command in dict.fromkeys(mf.TYPE_COMMANDS.values()):
        typed = [p for p in plan.planned if p.command == command]
        if not typed:
            continue
        ready = []
        for p in typed:
            if p.guid in run.failed_guids:
                run.skip([p], f"its story, layer or draw order change failed, so its "
                              f"{command} fields were not sent")
            else:
                ready.append(p)
        run.batches(command, ready,
                    lambda batch, c=command: {mf.LIST_KEYS[c]: [_modify_item(p) for p in batch]})

    levels = {s["index"]: float(s["level"]) for s in plan.stories}
    after = _read_details(conn, list(run.sent)) if run.sent else {}
    not_applied: dict[str, list[dict]] = {}
    partly: list[str] = []
    unverified: dict[str, int] = {}
    modified = 0
    for guid, p in run.sent.items():
        read = after.get(guid, {})
        mismatched = landed = False
        for name, expected in p.expected.items():
            actual = _read_field(p, name, read)
            if mf.values_match(expected, actual):
                before = _read_field(p, name, p.entry)
                landed = landed or not mf.values_match(expected, before)
                continue
            mismatched = True
            if guid not in run.failed_guids:
                not_applied.setdefault(name, []).append(
                    {"guid": guid, "sent": expected, "read": mf.summarize(actual)})
        for name in p.item:
            if name not in p.expected:
                unverified[name] = unverified.get(name, 0) + 1
        if p.keep_elevation and p.level_shift:
            height = _absolute_height(p.entry, levels)
            now = _absolute_height(read, levels) if read else None
            if height is None:
                unverified["keep_elevation"] = unverified.get("keep_elevation", 0) + 1
            elif now is None or not mf.values_match(height, now):
                mismatched = True
                if guid not in run.failed_guids:
                    not_applied.setdefault("keep_elevation", []).append(
                        {"guid": guid, "sent": height, "read": now})
        if guid in run.failed_guids:
            if landed:
                partly.append(guid)
        elif not mismatched and guid not in run.not_sent:
            modified += 1

    result: dict = {"dry_run": False, "requested": plan.requested, "modified": modified}
    if run.failed:
        result["failed"] = group_failures(run.failed, detail_key="command")
    if not_applied:
        result["not_applied"] = [
            {"field": name, "count": len(rows), "sample": rows[:FAILURE_SAMPLE]}
            for name, rows in sorted(not_applied.items(), key=lambda item: -len(item[1]))]
    if partly:
        shown, rest = cap_list(partly)
        result["partly_applied"] = {"count": len(partly), "guids": shown}
        if rest:
            result["partly_applied"]["not_shown"] = rest
    if unverified:
        result["unverified"] = dict(sorted(unverified.items()))
    if run.not_sent:
        result["not_sent"] = _group(run.not_sent)
    if mates:
        mates_after = _read_details(conn, mates)
        changed = [g for g in mates
                   if not _same(mates_before.get(g, {}), mates_after.get(g, {}))]
        if changed:
            shown, _ = cap_list(changed)
            result["group_mates_changed"] = {"count": len(changed), "guids": shown}
    if plan.skipped:
        result["skipped"] = _group(plan.skipped)
    if plan.warnings:
        result["warnings"] = _grouped_warnings(plan.warnings)
    if run.stopped is not None:
        result["stopped"] = error_fields(run.stopped)
    if plan.window is not None:
        result["active_window"] = plan.window
    return result
