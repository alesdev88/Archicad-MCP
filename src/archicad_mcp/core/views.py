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

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import _details
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK

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
