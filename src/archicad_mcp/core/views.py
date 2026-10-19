"""Show elements in Archicad: find the view that holds them, open it, select,
highlight and zoom.

Where an element lives. Tapir has no read of an element's database, but
GetAllElements takes `databases`: it switches only the API's current
database, never the window, and restores it afterwards (Tapir 1.7.1,
CommandBase.cpp ExecuteActionForEachDatabase). So the floor plan database is
listed once, and the other views' databases one at a time, found through the
navigator (GetNavigatorItemTree, GetDatabaseIdFromNavigatorItemId). Live on
MCP-Test_30, 19.10.2026: 0.03 to 0.3 s per database.

What Archicad says is not what it did. ChangeSelectionOfElements answered
success per element and left railing sub-elements, a floor-plan Object and
section Objects unselected; FitInWindow answered success for elements on
another story and in another database (19.10.2026). So the selection is read
back, and the story and window are confirmed after switching.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.actions import clear_highlight, fit_in_window, highlight_elements
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import _details, active_window
from archicad_mcp.extract import (
    PROPERTY_FETCH_CHUNK,
    element_payload,
    get_selected_element_ids,
)

# Tapir's fixed id for the floor plan database (CommandBase.cpp,
# DatabaseIdResolver). Every story shares it.
FLOOR_PLAN_DB = "D5D16DD4-093F-4674-895D-410D634D8C7E"

# Databases one call may list before giving up on the rest. A stale GUID on a
# big project would otherwise list every section, detail and layout.
WALK_CAP = 300

# Navigator items whose database can hold elements, in walk order.
PROJECT_MAP_VIEWS = ("SectionItem", "ElevationItem", "InteriorElevationItem",
                     "DetailDrawingItem", "WorksheetDrawingItem", "DocumentFrom3DItem")
LAYOUT_VIEWS = ("LayoutItem",)
MASTER_VIEWS = ("MasterLayoutItem",)

# The windows from which a GetDetailsOfElements read reaches every database
# (live 19.10.2026: layout and section elements read fine from the floor
# plan). From a Layout it missed floor-plan labels (28.09.2026), so from any
# other window an unreadable GUID is searched for anyway.
_READS_EVERYWHERE = ("FloorPlan", "3DModel")


@dataclass(frozen=True)
class View:
    kind: str                       # "FloorPlan", "3DModel" or a navigator item type
    name: str
    navigator_item_id: str | None
    database_id: str


FLOOR_PLAN = View("FloorPlan", "Floor Plan", None, FLOOR_PLAN_DB)
MODEL_3D = View("3DModel", "3D", None, "")


@dataclass
class Located:
    floor_plan: dict[str, int | None]   # guid -> story index (None: unreadable)
    elsewhere: dict[str, View]
    not_found: list[str]
    not_searched: list[str]
    views: list[View] = field(default_factory=list)  # views holding any, walk order


def _listing(conn: ArchicadConnection, database_id: str) -> set[str] | None:
    """Upper-case GUIDs of one database, or None when Archicad cannot list it."""
    try:
        response = conn.tapir("GetAllElements",
                              {"databases": [{"databaseId": {"guid": database_id}}]})
    except APIErrorBase:
        return None
    results = response.get("executionResultForDatabases") or []
    if results and results[0].get("success") is False:
        return None
    return {e["elementId"]["guid"].upper() for e in response.get("elements", [])}


def _read(conn: ArchicadConnection, guids: list[str]) -> dict[str, dict | None]:
    """guid -> its GetDetailsOfElements entry (type, floorIndex), None if unreadable."""
    out: dict[str, dict | None] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        items = _details(conn, chunk, ["type", "floorIndex"]).get("detailsOfElements", [])
        for i, guid in enumerate(chunk):
            item = items[i] if i < len(items) else None
            out[guid] = item if isinstance(item, dict) and "error" not in item else None
    return out


def _walk(node: dict, kinds: tuple[str, ...], out: list[dict]) -> None:
    if node.get("type") in kinds:
        out.append(node)
    for child in node.get("children", []):
        _walk(child.get("navigatorItem", {}), kinds, out)


def view_databases(conn: ArchicadConnection, layouts_first: bool = False) -> list[View]:
    """Every view whose database can hold elements, one per database, in walk
    order: Project Map views, layouts, master layouts (layouts first when
    asked). The floor plan is left out: it is listed on its own."""
    project_map = conn.tapir("GetNavigatorItemTree", {"navigatorMapId": "ProjectMap"})
    layout_book = conn.tapir("GetNavigatorItemTree", {"navigatorMapId": "LayoutBook"})
    groups: list[list[dict]] = [[], [], []]
    _walk(project_map.get("navigatorItemTree", {}), PROJECT_MAP_VIEWS, groups[0])
    _walk(layout_book.get("navigatorItemTree", {}), LAYOUT_VIEWS, groups[1])
    _walk(layout_book.get("navigatorItemTree", {}), MASTER_VIEWS, groups[2])
    order = (1, 2, 0) if layouts_first else (0, 1, 2)
    items = [item for g in order for item in groups[g]]
    if not items:
        return []
    response = conn.tapir("GetDatabaseIdFromNavigatorItemId", {
        "navigatorItemIds": [{"navigatorItemId": i["navigatorItemId"]} for i in items]})
    found: list[View] = []
    seen = {FLOOR_PLAN_DB}
    for item, entry in zip(items, response.get("databases", [])):
        database = ((entry or {}).get("databaseId") or {}).get("guid")
        if not database or database.upper() in seen:
            continue
        seen.add(database.upper())
        found.append(View(item["type"], item.get("name", ""),
                          item["navigatorItemId"]["guid"], database))
    return found


def locate(conn: ArchicadConnection, guids: list[str], window: str | None) -> Located:
    """Where each element lives: the floor plan (with its story), another view,
    or nowhere. GUIDs match ignoring case and are reported as given."""
    wanted: dict[str, str] = {}
    for g in guids:
        wanted.setdefault(g.upper(), g)
    plan = _listing(conn, FLOOR_PLAN_DB) or set()
    on_plan = [g for u, g in wanted.items() if u in plan]
    rest = [g for u, g in wanted.items() if u not in plan]
    read = _read(conn, on_plan + rest) if wanted else {}
    floor_plan = {g: (read[g] or {}).get("floorIndex") for g in on_plan}
    floor_plan = {g: int(s) if isinstance(s, (int, float)) else None
                  for g, s in floor_plan.items()}

    not_found: list[str] = []
    if window in _READS_EVERYWHERE:
        not_found = [g for g in rest if read.get(g) is None]
        rest = [g for g in rest if read.get(g) is not None]
    located = Located(floor_plan, {}, not_found, [])
    if not rest:
        return located

    drawings_only = all((read.get(g) or {}).get("type") == "Drawing" for g in rest)
    try:
        candidates = view_databases(conn, layouts_first=drawings_only)
    except APIErrorBase:
        located.not_searched = rest
        return located
    left = {g.upper(): g for g in rest}
    complete = True
    for searched, view in enumerate(candidates):
        if not left:
            break
        if searched >= WALK_CAP:
            complete = False
            break
        listed = _listing(conn, view.database_id)
        if listed is None:
            complete = False
            continue
        hits = [u for u in left if u in listed]
        for u in hits:
            located.elsewhere[left.pop(u)] = view
        if hits:
            located.views.append(view)
    remaining = list(left.values())
    if complete:
        located.not_found.extend(remaining)
    else:
        located.not_searched = remaining
    return located


def choose(located: Located, window: str | None,
           active_story: int) -> tuple[View | None, int | None]:
    """The view to show and, on the floor plan, the story.

    The view holding the most elements wins; a tie goes to the floor plan,
    then to the view met first in the walk. On the floor plan, the story
    holding the most wins, a tie going to the active story, then the lowest
    index. With the 3D window in front, a floor-plan win stays in 3D, which
    shows every story. (None, None) when nothing was located.
    """
    per_view = Counter(v.database_id for v in located.elsewhere.values())
    best: tuple[View | None, int] = (None, 0)
    if located.floor_plan:
        best = (FLOOR_PLAN, len(located.floor_plan))
    for view in located.views:
        if per_view[view.database_id] > best[1]:
            best = (view, per_view[view.database_id])
    view = best[0]
    if view is None:
        return None, None
    if view is not FLOOR_PLAN:
        return view, None
    if window == "3DModel":
        return MODEL_3D, None
    stories = Counter(s for s in located.floor_plan.values() if s is not None)
    if not stories:
        return FLOOR_PLAN, active_story
    top = max(stories.values())
    leaders = [s for s, n in stories.items() if n == top]
    return FLOOR_PLAN, active_story if active_story in leaders else min(leaders)


# The window a navigator view opens, as GetCurrentWindowType names it.
WINDOW_OF = {"SectionItem": "Section", "ElevationItem": "Elevation",
             "InteriorElevationItem": "InteriorElevation", "DetailDrawingItem": "Details",
             "WorksheetDrawingItem": "Worksheet", "DocumentFrom3DItem": "3DDocument",
             "LayoutItem": "Layout", "MasterLayoutItem": "MasterLayout"}


def _story_names(stories: dict) -> dict[int, str]:
    return {s["index"]: s.get("name") or f"Story {s['index']}"
            for s in stories.get("stories", []) if "index" in s}


def _label(window: str | None, story: int | None, names: dict[int, str],
           view: View | None = None) -> str:
    if view is not None and view.kind in WINDOW_OF:
        return f"{WINDOW_OF[view.kind]} '{view.name}'"
    if window == "FloorPlan" and story is not None:
        return f"FloorPlan, story {story} ({names.get(story, f'Story {story}')})"
    return window or "an unknown window"


def _go_to_story(conn: ArchicadConnection, story: int) -> None:
    try:
        conn.tapir("ChangeWindow", {"windowType": "FloorPlan", "storyIndex": story})
    except APIErrorBase:
        # Tapir before 1.5.4 has no storyIndex: bring the floor plan forward at
        # least, and let the story check below say what is left to do.
        conn.tapir("ChangeWindow", {"windowType": "FloorPlan"})


def _replace_selection(conn: ArchicadConnection, guids: list[str]) -> tuple[int, list[str]]:
    """Select exactly `guids`; (how many Archicad really selected, the rest).

    Tapir adds before it removes, so an element in both lists would end up
    deselected: only what is not wanted is removed.
    """
    wanted = {g.upper() for g in guids}
    current = get_selected_element_ids(conn)
    conn.tapir("ChangeSelectionOfElements", {
        "addElementsToSelection": element_payload(guids),
        "removeElementsFromSelection": element_payload(
            [g for g in current if g.upper() not in wanted])})
    now = {g.upper() for g in get_selected_element_ids(conn)}
    missed = [g for g in guids if g.upper() not in now]
    return len(guids) - len(missed), missed


def clear(conn: ArchicadConnection, select: bool) -> dict:
    """Remove the highlight and, with select, the selection."""
    clear_highlight(conn)
    out: dict = {"cleared": True, "shown": 0}
    if select:
        current = get_selected_element_ids(conn)
        if current:
            conn.tapir("ChangeSelectionOfElements",
                       {"removeElementsFromSelection": element_payload(current)})
        out["deselected"] = len(current)
    return out


def show_elements(conn: ArchicadConnection, guids: list[str], select: bool = True,
                  highlight: bool = True, zoom: bool = True) -> dict:
    if not guids:
        return clear(conn, select)
    window = active_window(conn)
    stories = conn.tapir("GetStories")
    names = _story_names(stories)
    act = stories.get("actStory", 0)
    located = locate(conn, guids, window)
    view, story = choose(located, window, act)
    warnings: list[str] = []
    out: dict = {"shown": 0, "view": None, "switched": False}

    if view is None:
        warnings.append("None of the elements was found in the floor plan or in any "
                        "view; nothing was shown.")
        shown: list[str] = []
        marked: list[str] = []
    elif view is MODEL_3D:
        shown = marked = list(located.floor_plan)
        out["view"] = {"type": "3DModel"}
    elif view is FLOOR_PLAN:
        marked = list(located.floor_plan)
        shown = [g for g, s in located.floor_plan.items() if s in (story, None)]
        out["view"] = {"type": "FloorPlan", "story_index": story,
                       "story_name": names.get(story, f"Story {story}")}
        if window != "FloorPlan" or act != story:
            _go_to_story(conn, story)
            now = conn.tapir("GetStories").get("actStory")
            now_window = active_window(conn)
            out["switched"] = {"from": _label(window, act, names),
                               "to": _label(now_window, now, names)}
            if now != story or now_window != "FloorPlan":
                warnings.append(
                    f"Archicad did not open FloorPlan, story {story} "
                    f"({names.get(story, f'Story {story}')}): it shows "
                    f"{_label(now_window, now, names)}. Open that story by hand; Tapir "
                    "before 1.7.0 does not change the story (#748).")
    else:
        shown = marked = [g for g, v in located.elsewhere.items() if v == view]
        expected = WINDOW_OF.get(view.kind)
        out["view"] = {"type": expected, "name": view.name,
                       "navigator_item_id": view.navigator_item_id}
        conn.tapir("ChangeWindow", {"navigatorItemId": {"guid": view.navigator_item_id}})
        now_window = active_window(conn)
        out["switched"] = {"from": _label(window, act, names),
                           "to": _label(now_window, None, names, view)}
        if now_window != expected:
            warnings.append(f"Archicad shows {now_window or 'an unknown window'}, not "
                            f"{expected} '{view.name}'; open it from the Navigator.")

    out["shown"] = len(shown)
    if view is not None:
        if select:
            out["selected"], out["not_selectable"] = _replace_selection(conn, marked)
        if highlight:
            out["highlighted"] = highlight_elements(conn, marked)["highlighted"]
        else:
            # What is on screen should match this call, not an earlier one.
            clear_highlight(conn)
            out["highlighted"] = 0
    out["zoomed"] = bool(zoom and shown) and fit_in_window(conn, shown)

    other: dict[int, list[str]] = {}
    if view is FLOOR_PLAN:
        for g, s in located.floor_plan.items():
            if s not in (story, None):
                other.setdefault(s, []).append(g)
    out["on_other_stories"] = [{"story_index": s, "story_name": names.get(s, f"Story {s}"),
                                "guids": gs} for s, gs in sorted(other.items())]
    if view not in (FLOOR_PLAN, MODEL_3D) and located.floor_plan:
        out["in_other_views"] = [{"type": "FloorPlan", "name": "Floor Plan",
                                  "guids": list(located.floor_plan)}]
    else:
        out["in_other_views"] = []
    for v in located.views:
        if v != view:
            out["in_other_views"].append({"type": WINDOW_OF.get(v.kind, v.kind),
                                          "name": v.name,
                                          "guids": [g for g, w in located.elsewhere.items()
                                                    if w == v]})
    out["not_found"] = located.not_found
    out["not_searched"] = located.not_searched
    if located.not_searched:
        warnings.append(f"{len(located.not_searched)} element(s) were not searched for: "
                        f"the walk stops after {WALK_CAP} views, or a view could not be "
                        "listed. Show them from the view that holds them.")
    out["warnings"] = warnings
    return out
