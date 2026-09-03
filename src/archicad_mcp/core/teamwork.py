"""Teamwork element reservation over Tapir ReserveElements / ReleaseElements.

What Tapir reports, read from its source (TeamworkCommands.cpp, 1.5.9): a
reservation attempt returns only the elements it could NOT reserve because
another user holds them, with that user's name. It says nothing about
elements that did not exist, elements already in the caller's workspace, or
elements Archicad reserved on the side (a door's wall, a wall's doors). Those
three are derived here:

* not found: GUIDs the official GetTypesOfElements does not know.
* already mine: Tapir FilterElements with InMyWorkspace, before the attempt.
* indirectly reserved: the elements newly in my workspace after the attempt
  that were not asked for. Computed by diffing InMyWorkspace over the whole
  plan, which is FilterElements in 2000-element chunks and never touches a
  property value (63k elements took seconds live).

There is no read that says who holds an element without trying to reserve
it: ACAPI_Teamwork_GetLockableStatus covers lockable sets (attributes), not
elements. So the dry run can report not-found and already-mine, and must say
that reserved-by-other is only learned by attempting.

Confirm-gated rather than dry-run-by-default in name only: a reservation is
visible to every teammate and blocks their edits until released.
"""
from __future__ import annotations

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


def reserve_elements(conn: ArchicadConnection, guids: list[str],
                     confirm: bool = False) -> dict:
    if (err := _not_teamwork_error(conn)) is not None:
        return err
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


def release_elements(conn: ArchicadConnection, guids: list[str],
                     confirm: bool = False) -> dict:
    if (err := _not_teamwork_error(conn)) is not None:
        return err
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
