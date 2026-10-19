"""Send a modify_elements plan and report what Archicad actually did.

Order: SetDetailsOfElements (story, layer, draw order), then the
keep_elevation move, then the Modify commands, then a read of every sent
element. SetDetailsOfElements answers doors, windows and skylights that need
their host first (ElementCommands.cpp pre-scan, 1.5.9 to 1.7.1), so each batch
is sent in that order and results still match by position. Draw order steps
answer success whether or not they moved anything, and Archicad discards some
fields silently, so success is not trusted: the readback decides `modified`.

A command can also stop the run (a dropped connection, Archicad answering
4001 while someone works in it). The report must survive that: each field is
judged only against the command that carried it, what landed before the stop
is reported as partly applied, and if even the readback fails the sent
elements are listed as unconfirmed rather than lost behind an error.
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
SET_DETAILS = "SetDetailsOfElements"
MOVE = "MoveElements"
_COMMON = ("story", "layer", "draw_order")


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


def _command_of(p: Planned, name: str) -> str | None:
    """The command that carries a field of the plan."""
    if name in _COMMON:
        return SET_DETAILS
    if name == "keep_elevation":
        return MOVE
    return p.command


class _Run:
    """The state of one commit: what was sent with which command, what failed,
    what stopped it."""

    def __init__(self, conn: ArchicadConnection):
        self.conn = conn
        self.sent: dict[str, Planned] = {}
        self.dispatched: dict[str, set[str]] = {}       # GUID -> commands sent for it
        self.failed: list[dict] = []
        self.failed_by: set[tuple[str, str]] = set()     # (GUID, command)
        self.unanswered: set[tuple[str, str]] = set()    # in a batch that raised
        self.not_sent: dict[str, str] = {}
        self.stopped: APIErrorBase | ArchicadUnavailableError | None = None
        self.suspend_left_on: str | None = None

    def failed_guid(self, guid: str) -> bool:
        return any(g == guid for g, _ in self.failed_by)

    def send(self, command: str, batch: list[Planned], payload: dict) -> None:
        """One batch; per-element results in `batch` order."""
        for p in batch:
            self.sent[p.guid] = p
            self.dispatched.setdefault(p.guid, set()).add(command)
        try:
            response = self.conn.tapir(command, payload)
        except _ERRORS as exc:
            # Archicad may have acted before the error; the readback says.
            self.stopped = exc
            self.unanswered.update((p.guid, command) for p in batch)
            return
        results = (response or {}).get("executionResults", [])
        for i, p in enumerate(batch):
            outcome = results[i] if i < len(results) else None
            if isinstance(outcome, dict) and outcome.get("success") is True:
                continue
            error = (outcome.get("error") or {}) if isinstance(outcome, dict) else {}
            self.failed.append({
                "guid": p.guid, "command": command, "code": error.get("code"),
                "message": error.get("message", "Archicad returned no result for it")})
            self.failed_by.add((p.guid, command))

    def batches(self, command: str, planned: list[Planned], build) -> None:
        for start in range(0, len(planned), PROPERTY_FETCH_CHUNK):
            if self.stopped is not None:
                self.skip(planned[start:], self.stop_reason())
                return
            batch = planned[start:start + PROPERTY_FETCH_CHUNK]
            self.send(command, batch, build(batch))

    def skip(self, planned: list[Planned], reason: str) -> None:
        for p in planned:
            self.not_sent.setdefault(p.guid, reason)

    def stop_reason(self) -> str:
        return ("not sent: the run stopped at an earlier command "
                f"({error_fields(self.stopped)['message']})")


def _suspend(run: _Run, on: bool) -> str | None:
    """Set Suspend Groups; None when it took, else why not. Tapir answers a
    refusal as executionResult success false, without raising."""
    try:
        response = run.conn.tapir("SetSuspendGroupsMode", {"suspendGroups": on})
    except _ERRORS as exc:
        run.stopped = run.stopped or exc
        return error_fields(exc)["message"]
    result = (response or {}).get("executionResult") or {}
    if result.get("success") is False:
        return (result.get("error") or {}).get("message", "Archicad refused it")
    return None


def _move_back(run: _Run, plan: Plan) -> None:
    """Undo the story change's height shift for keep_elevation elements, with
    Suspend Groups on around it so the move does not drag a group along."""
    movers = [p for p in plan.planned if p.keep_elevation and p.level_shift
              and SET_DETAILS in run.dispatched.get(p.guid, ())
              and (p.guid, SET_DETAILS) not in run.failed_by | run.unanswered]
    if not movers:
        return
    if run.stopped is not None:
        run.skip(movers, run.stop_reason())
        return
    suspend = plan.suspend_groups is False and any(p.guid in plan.groups for p in movers)
    if suspend:
        problem = _suspend(run, True)
        if problem is not None:
            run.skip(movers, f"not moved back to its height: Suspend Groups could not be "
                             f"turned on ({problem}), and the move would have dragged "
                             "the whole group")
            return
    payload = {"elementsWithMoveVectors": [
        {"elementId": {"guid": p.guid}, "moveVector": {"x": 0, "y": 0, "z": -p.level_shift}}
        for p in movers]}
    try:
        run.send(MOVE, movers, payload)
    finally:
        if suspend:
            problem = _suspend(run, False)
            if problem is not None:
                run.suspend_left_on = (
                    f"left on: turning it off again failed ({problem}); turn it off in "
                    "Archicad (Edit > Grouping > Suspend Groups)")


def _blocked(run: _Run, guid: str, command: str) -> str | None:
    """Why an element's Modify write must not follow, or None."""
    if (guid, SET_DETAILS) in run.failed_by | run.unanswered:
        return (f"its story, layer or draw order change failed, so its {command} "
                "fields were not sent")
    if (guid, MOVE) in run.failed_by | run.unanswered or guid in run.not_sent:
        return (f"its move back to the same height failed, so its {command} fields "
                "were not sent")
    return None


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


class _Verdict:
    """The readback's findings across all sent elements."""

    def __init__(self):
        self.modified = 0
        self.not_applied: dict[str, list[dict]] = {}
        self.partly: list[str] = []
        self.unverified: dict[str, int] = {}

    def count_unverified(self, name: str) -> None:
        self.unverified[name] = self.unverified.get(name, 0) + 1


def _judge(run: _Run, p: Planned, read: dict, levels: dict[int, float],
           verdict: _Verdict) -> None:
    """Sort one sent element: modified, partly applied, or neither."""
    sent = run.dispatched.get(p.guid, set())
    checks = list(p.expected.items())
    if p.keep_elevation and p.level_shift:
        checks.append(("keep_elevation", _absolute_height(p.entry, levels)))
    landed = pending = False
    for name, expected in checks:
        command = _command_of(p, name)
        if command not in sent:
            pending = True
            continue
        if name == "keep_elevation":
            if expected is None:
                verdict.count_unverified(name)
                continue
            actual = _absolute_height(read, levels) if read else None
            before = None
        else:
            actual = _read_field(p, name, read)
            before = _read_field(p, name, p.entry)
        if mf.values_match(expected, actual):
            landed = landed or not mf.values_match(expected, before)
            continue
        pending = True
        if (p.guid, command) in run.failed_by:
            continue                       # in `failed` already
        if (p.guid, command) in run.unanswered:
            run.not_sent.setdefault(p.guid, (
                f"{command} stopped before Archicad answered "
                f"({error_fields(run.stopped)['message']}), and the read shows it did "
                "not land"))
            continue
        verdict.not_applied.setdefault(name, []).append(
            {"guid": p.guid, "sent": expected, "read": mf.summarize(actual)})
    for name in p.item:
        if name not in p.expected and p.command in sent:
            verdict.count_unverified(name)
    if p.draw_order == 0 and SET_DETAILS in sent:
        verdict.count_unverified("draw_order")
    if pending or run.failed_guid(p.guid) or p.guid in run.not_sent:
        if landed:
            verdict.partly.append(p.guid)
    else:
        verdict.modified += 1


def commit(conn: ArchicadConnection, plan: Plan) -> dict:
    run = _Run(conn)
    mates = _group_mates(conn, plan)
    mates_before = _read_details(conn, mates) if mates else {}

    common = [p for p in plan.planned
              if p.story is not None or p.layer is not None or p.draw_order is not None]
    run.batches(SET_DETAILS, setdetails_order(common),
                lambda batch: {"elementsWithDetails": [_setdetails_item(p) for p in batch]})
    _move_back(run, plan)
    for command in dict.fromkeys(mf.TYPE_COMMANDS.values()):
        ready = []
        for p in (p for p in plan.planned if p.command == command):
            reason = _blocked(run, p.guid, command)
            if reason is None:
                ready.append(p)
            else:
                run.skip([p], reason)
        if ready:
            run.batches(command, ready,
                        lambda batch, c=command: {mf.LIST_KEYS[c]: [_modify_item(p)
                                                                    for p in batch]})

    result: dict = {"dry_run": False, "requested": plan.requested, "modified": 0}
    levels = {s["index"]: float(s["level"]) for s in plan.stories}
    try:
        after = _read_details(conn, list(run.sent)) if run.sent else {}
        mates_after = _read_details(conn, mates) if mates else {}
    except _ERRORS as exc:
        run.stopped = run.stopped or exc
        shown, rest = cap_list(list(run.sent))
        result["unconfirmed"] = {
            "count": len(run.sent), "guids": shown,
            "note": ("the read after the commit failed, so what landed is unknown; "
                     "run a dry run before retrying, since a story change may already "
                     "have moved these elements")}
        if rest:
            result["unconfirmed"]["not_shown"] = rest
        after = mates_after = None

    if after is not None:
        verdict = _Verdict()
        for guid, p in run.sent.items():
            _judge(run, p, after.get(guid, {}), levels, verdict)
        result["modified"] = verdict.modified
        if verdict.not_applied:
            result["not_applied"] = [
                {"field": name, "count": len(rows), "sample": rows[:FAILURE_SAMPLE]}
                for name, rows in sorted(verdict.not_applied.items(),
                                         key=lambda item: -len(item[1]))]
        if verdict.partly:
            shown, rest = cap_list(verdict.partly)
            result["partly_applied"] = {"count": len(verdict.partly), "guids": shown}
            if rest:
                result["partly_applied"]["not_shown"] = rest
        if verdict.unverified:
            result["unverified"] = dict(sorted(verdict.unverified.items()))
    if run.failed:
        result["failed"] = group_failures(run.failed, detail_key="command")
    if run.not_sent:
        result["not_sent"] = _group(run.not_sent)
    if mates_after:
        changed = [g for g in mates
                   if not _same(mates_before.get(g, {}), mates_after.get(g, {}))]
        if changed:
            shown, rest = cap_list(changed)
            result["group_mates_changed"] = {"count": len(changed), "guids": shown}
            if rest:
                result["group_mates_changed"]["not_shown"] = rest
    if run.suspend_left_on is not None:
        result["suspend_groups"] = run.suspend_left_on
    if plan.skipped:
        result["skipped"] = _group(plan.skipped)
    if plan.warnings:
        result["warnings"] = _grouped_warnings(plan.warnings)
    if run.stopped is not None:
        result["stopped"] = error_fields(run.stopped)
    if plan.window is not None:
        result["active_window"] = plan.window
    return result
