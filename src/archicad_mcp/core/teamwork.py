"""Teamwork element reservation over Tapir ReserveElements / ReleaseElements.

What Tapir reports, read from its source (TeamworkCommands.cpp, 1.5.9): a
reservation attempt returns only the elements it could NOT reserve because
another user holds them, with that user's name. It says nothing about
elements that did not exist, elements already in the caller's workspace, or
elements Archicad reserved on the side (a door's wall, a wall's doors). Those
three are derived here:

* not found: GUIDs the official GetTypesOfElements answers 7204 "Element not
  found" for. Its 7203 "Element not supported" (every 2D element and native
  MEP element, live) means the element exists, so those are attempted too.
* already mine: Tapir FilterElements with InMyWorkspace, before the attempt.
* indirectly reserved: the elements newly in my workspace after the attempt
  that were not asked for. Computed by diffing InMyWorkspace over the whole
  plan, which is FilterElements in 2000-element chunks and never touches a
  property value (63k elements took seconds live).

There is no read that says who holds an element without trying to reserve
it: ACAPI_Teamwork_GetLockableStatus covers lockable sets (attributes), not
elements. So the dry run can report not-found and already-mine, and must say
that reserved-by-other is only learned by attempting.

Non-element lockable object sets (attribute types, property and
classification definitions, favourites, project info, ...) go through Tapir
1.7.1's ReserveLockableObjectSet, ReleaseLockableObjectSet and
GetLockableObjectSetStatus. Their holder CAN be read without attempting, so
the dry run for sets is complete. Outside Teamwork, FindLockableObjectSet
finds no set at all (every name answers "No lockable object set named ...",
live on MCP-Test 19.10.2026), so nothing here asks unless isTeamwork.

Confirm-gated rather than dry-run-by-default in name only: a reservation is
visible to every teammate and blocks their edits until released.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import _fetch_types, element_payload, get_all_element_ids

FILTER_CHUNK = 2000

_ATTEMPT_NOTE = ("Whether another user holds an element is only learned by "
                 "attempting the reservation; Archicad exposes no read for it. "
                 "Pass confirm=true to attempt.")


def _is_teamwork(conn: ArchicadConnection) -> bool:
    return bool(conn.tapir("GetProjectInfo").get("isTeamwork"))


def _not_teamwork_error(conn: ArchicadConnection) -> dict | None:
    if not _is_teamwork(conn):
        return {"error": "The open project is not a Teamwork project, so there is "
                         "nothing to reserve or release."}
    return None


def _in_my_workspace(conn: ArchicadConnection, guids: list[str]) -> set[str]:
    mine: set[str] = set()
    for start in range(0, len(guids), FILTER_CHUNK):
        chunk = guids[start:start + FILTER_CHUNK]
        response = conn.tapir("FilterElements", {"elements": element_payload(chunk),
                                                 "filters": ["InMyWorkspace"]})
        mine.update(e["elementId"]["guid"] for e in response.get("elements", []))
    return mine


def _partition(conn: ArchicadConnection, guids: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(known, not_found, already_mine) in the caller's order, deduplicated."""
    guids = list(dict.fromkeys(guids))
    types = _fetch_types(conn, guids) if guids else {}
    known = [g for g in guids if g in types]
    not_found = [g for g in guids if g not in types]
    mine = _in_my_workspace(conn, known) if known else set()
    return known, not_found, [g for g in known if g in mine]


def _execution_error(response: dict, what: str) -> dict | None:
    result = response.get("executionResult") or {}
    if result and not result.get("success", True):
        detail = result.get("error", {})
        return {"error": f"Archicad refused to {what}: {detail.get('message', detail)}"}
    return None


def _reserve_guids(conn: ArchicadConnection, guids: list[str], confirm: bool) -> dict:
    known, not_found, already_mine = _partition(conn, guids)
    candidates = [g for g in known if g not in set(already_mine)]
    base = {"requested": len(dict.fromkeys(guids)), "not_found": not_found,
            "already_mine": already_mine}
    if not confirm:
        return {"dry_run": True, **base, "would_attempt": candidates,
                "note": _ATTEMPT_NOTE}
    if not candidates:
        return {"dry_run": False, **base, "reserved": [], "reserved_by_others": [],
                "indirectly_reserved": []}

    plan = get_all_element_ids(conn)
    before = _in_my_workspace(conn, plan)
    response = conn.tapir("ReserveElements", {"elements": element_payload(candidates)})
    if (err := _execution_error(response, "reserve elements")) is not None:
        return err
    conflicts = []
    blocked = set()
    for item in response.get("conflicts", []):
        guid = item.get("elementId", {}).get("guid", "")
        blocked.add(guid)
        conflicts.append({"guid": guid, "user": item.get("user", {}).get("userName"),
                          "user_id": item.get("user", {}).get("userId")})
    after = _in_my_workspace(conn, plan)
    asked = set(candidates)
    reserved = [g for g in candidates if g in after and g not in blocked]
    indirect = sorted(g for g in after - before if g not in asked)
    return {"dry_run": False, **base, "reserved": reserved,
            "reserved_by_others": conflicts, "indirectly_reserved": indirect}


def _release_guids(conn: ArchicadConnection, guids: list[str], confirm: bool) -> dict:
    known, not_found, mine = _partition(conn, guids)
    not_mine = [g for g in known if g not in set(mine)]
    base = {"requested": len(dict.fromkeys(guids)), "not_found": not_found,
            "not_mine": not_mine}
    if not confirm:
        return {"dry_run": True, **base, "would_release": mine,
                "note": "Only elements in your workspace can be released. "
                        "Pass confirm=true to release them."}
    if not mine:
        return {"dry_run": False, **base, "released": [], "still_mine": []}
    response = conn.tapir("ReleaseElements", {"elements": element_payload(mine)})
    if (err := _execution_error(response, "release elements")) is not None:
        return err
    still = _in_my_workspace(conn, mine)
    return {"dry_run": False, **base,
            "released": [g for g in mine if g not in still],
            "still_mine": [g for g in mine if g in still]}


# ---------- lockable object sets ----------

# Alias (lowercase, no spaces, dashes or underscores) -> the name Archicad's
# ACAPI_Teamwork_FindLockableObjectSet takes. Layers and layer combinations are
# one compound set, and the DevKit says a member of a compound set must not be
# reserved alone, so both go to LayerSettingsDialog.
_SET_ALIASES = {
    **dict.fromkeys(("layer", "layers", "layercombination", "layercombinations",
                     "layersettingsdialog"), "LayerSettingsDialog"),
    **dict.fromkeys(("line", "lines", "linetype", "linetypes"), "LineTypes"),
    **dict.fromkeys(("fill", "fills", "filltype", "filltypes"), "FillTypes"),
    **dict.fromkeys(("composite", "composites"), "Composites"),
    **dict.fromkeys(("pentable", "pentables"), "PenTables"),
    **dict.fromkeys(("profile", "profiles"), "Profiles"),
    **dict.fromkeys(("buildingmaterial", "buildingmaterials"), "BuildingMaterials"),
    **dict.fromkeys(("surface", "surfaces"), "Surfaces"),
    **dict.fromkeys(("zonecategory", "zonecategories"), "ZoneCategories"),
    # Not in the DevKit's list. Archicad 29's APICore string table has it next
    # to the documented names, and no documented set covers property or
    # classification definitions. Unverified until a Teamwork project answers.
    **dict.fromkeys(("properties", "property", "propertydefinitions", "classifications",
                     "classification", "classificationsandproperties"),
                    "ClassificationsAndProperties"),
}

DEFINITIONS_SET = "ClassificationsAndProperties"

_SET_COMMAND = "ReserveLockableObjectSet"
_STATUS_COMMAND = "GetLockableObjectSetStatus"

_SET_RESERVE_NOTE = ("Pass confirm=true to reserve the sets marked would_reserve. A "
                     "reservation is visible to the team and blocks their edits of that "
                     "set until you release it.")
_SET_RELEASE_NOTE = ("Releasing a set sends its changes to the Teamwork server first; "
                     "Archicad does that as part of the release.")


def canonical_set(name: str) -> str:
    """The Archicad name of a lockable object set; unknown names pass through."""
    key = "".join(ch for ch in name.strip().lower() if ch not in " -_")
    return _SET_ALIASES.get(key, name.strip())


def reserve_call(set_name: str) -> str:
    return f'reserve_elements(object_sets=["{set_name}"], confirm=true)'


@dataclass
class SetState:
    name: str
    status: str | None          # Tapir's status; None when it could not be read
    held_by: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SetProblem:
    text: str
    # True when Archicad is known to refuse the write (the set is free or
    # another user's); False when the status could not be learned.
    blocking: bool


def _holders(response: dict) -> list[str]:
    return [c.get("userName", "") for c in response.get("conflicts", [])]


def _read_state(conn: ArchicadConnection, name: str) -> SetState:
    try:
        response = conn.tapir(_STATUS_COMMAND, {"objectSetName": name})
    except APIErrorBase as exc:
        return SetState(name, None, error=exc.message)
    return SetState(name, response.get("status"), _holders(response))


def object_set_states(conn: ArchicadConnection,
                      names: list[str]) -> dict[str, SetState] | None:
    """Each set's reservation state, by Archicad name; None outside Teamwork."""
    if not _is_teamwork(conn):
        return None
    names = list(dict.fromkeys(canonical_set(n) for n in names))
    if not conn.tapir_command_available(_STATUS_COMMAND):
        reason = (f"this Tapir cannot read Teamwork reservations of non-element sets "
                  f"({_STATUS_COMMAND} arrived in Tapir 1.7.1)")
        return {n: SetState(n, None, error=reason) for n in names}
    return {n: _read_state(conn, n) for n in names}


def set_problem(state: SetState) -> SetProblem | None:
    """Why a write needing this set may not go through; None when it is mine."""
    if state.status == "Editable":
        return None
    if state.status == "Free":
        return SetProblem(f"the {state.name} set is not reserved; reserve it first with "
                          f"{reserve_call(state.name)}", True)
    if state.status == "Locked":
        who = ", ".join(state.held_by) or "another user"
        return SetProblem(f"the {state.name} set is reserved by {who}; they must release "
                          "it first", True)
    if state.status in ("NotAvailable", "NotExist"):
        return SetProblem(f"the Teamwork server is unavailable, so whether the {state.name} "
                          "set is reserved is unknown", False)
    return SetProblem(f"could not check the reservation of the {state.name} set: "
                      f"{state.error}", False)


def teamwork_entries(states: dict[str, SetState],
                     needed_for: dict[str, list[str]]) -> list[dict]:
    """The `teamwork` list a write tool's result carries in a Teamwork project."""
    out = []
    for name, state in states.items():
        entry = {"set": name, "status": state.status, "held_by": state.held_by,
                 "needed_for": needed_for.get(name, [])}
        problem = set_problem(state)
        if problem:
            entry["problem"] = problem.text
        out.append(entry)
    return out


def _set_entries(conn: ArchicadConnection, asked: list[str]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for name in asked:
        canonical = canonical_set(name)
        entries.setdefault(canonical, {"set": canonical, "asked": []})["asked"].append(name)
    for canonical, entry in entries.items():
        _apply_state(entry, _read_state(conn, canonical))
    return entries


def _apply_state(entry: dict, state: SetState) -> None:
    entry["status"] = state.status
    entry["held_by"] = state.held_by
    if state.error:
        entry["error"] = state.error


_UNREADABLE = {None: "error", "NotAvailable": "server_unavailable",
               "NotExist": "server_unavailable"}


def _reserve_sets(conn: ArchicadConnection, asked: list[str], confirm: bool) -> list[dict]:
    entries = _set_entries(conn, asked)
    for name, entry in entries.items():
        status = entry["status"]
        if status in _UNREADABLE:
            entry["outcome"] = _UNREADABLE[status]
        elif status == "Editable":
            entry["outcome"] = "already_mine"
        elif status == "Locked":
            entry["outcome"] = "held_by_others"
        elif not confirm:
            entry["outcome"] = "would_reserve"
        else:
            _attempt_reserve(conn, name, entry)
    return list(entries.values())


def _attempt_reserve(conn: ArchicadConnection, name: str, entry: dict) -> None:
    try:
        response = conn.tapir(_SET_COMMAND, {"objectSetName": name})
    except APIErrorBase as exc:
        response = {"executionResult": {"success": False, "error": {"message": exc.message}}}
    # What the server now says, not what the command answered.
    _apply_state(entry, _read_state(conn, name))
    if entry["status"] == "Editable":
        entry["outcome"] = "reserved"
        return
    entry["outcome"] = "failed"
    if response.get("conflicts"):
        entry["held_by"] = _holders(response)
    detail = (response.get("executionResult") or {}).get("error") or {}
    entry["error"] = detail.get("message") or entry.get("error") or "reservation refused"


def _release_sets(conn: ArchicadConnection, asked: list[str], confirm: bool) -> list[dict]:
    entries = _set_entries(conn, asked)
    for name, entry in entries.items():
        status = entry["status"]
        if status in _UNREADABLE:
            entry["outcome"] = _UNREADABLE[status]
        elif status != "Editable":
            entry["outcome"] = "not_mine"
        elif not confirm:
            entry["outcome"] = "would_release"
        else:
            try:
                response = conn.tapir("ReleaseLockableObjectSet", {"objectSetName": name})
            except APIErrorBase as exc:
                response = {"success": False, "error": {"message": exc.message}}
            _apply_state(entry, _read_state(conn, name))
            if entry["status"] != "Editable":
                entry["outcome"] = "released"
            else:
                entry["outcome"] = "failed"
                entry["error"] = (response.get("error") or {}).get("message",
                                                                    "release refused")
    return list(entries.values())


def _with_note(result: dict, note: str) -> dict:
    result["note"] = f"{result['note']} {note}" if result.get("note") else note
    return result


def _set_gate(conn: ArchicadConnection, guids, object_sets, tool: str) -> dict | None:
    if not guids and not object_sets:
        return {"error": "Give guids or object_sets (or both)."}
    if (err := _not_teamwork_error(conn)) is not None:
        return err
    if object_sets:
        from archicad_mcp.tapir_requirements import require_tapir_command
        return require_tapir_command(conn, _SET_COMMAND, tool)
    return None


def reserve_elements(conn: ArchicadConnection, guids: list[str] | None,
                     confirm: bool = False, object_sets: list[str] | None = None) -> dict:
    if (err := _set_gate(conn, guids, object_sets, "reserve_elements")) is not None:
        return err
    result = _reserve_guids(conn, guids, confirm) if guids else {"dry_run": not confirm}
    if "error" in result or not object_sets:
        return result
    result["object_sets"] = _reserve_sets(conn, object_sets, confirm)
    return result if confirm else _with_note(result, _SET_RESERVE_NOTE)


def release_elements(conn: ArchicadConnection, guids: list[str] | None,
                     confirm: bool = False, object_sets: list[str] | None = None) -> dict:
    if (err := _set_gate(conn, guids, object_sets, "release_elements")) is not None:
        return err
    result = _release_guids(conn, guids, confirm) if guids else {"dry_run": not confirm}
    if "error" in result or not object_sets:
        return result
    result["object_sets"] = _release_sets(conn, object_sets, confirm)
    return _with_note(result, _SET_RELEASE_NOTE)
