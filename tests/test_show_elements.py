"""show_elements: find where elements live, pick the view, then show them."""
from __future__ import annotations

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import views
from archicad_mcp.core.views import FLOOR_PLAN_DB, Located, View
from tests.conftest import FakeCore
from tests.fixtures import api_replays

SEC_DB, SEC2_DB, LAY_DB, MAS_DB = "SEC-DB", "SEC2-DB", "LAY-DB", "MAS-DB"


def _item(kind, name, nav, children=()):
    node = {"navigatorItemId": {"guid": nav}, "type": kind, "name": name, "prefix": "",
            "uiId": "", "customUiId": False, "customName": False, "isIndependent": False}
    if children:
        node["children"] = [{"navigatorItem": c} for c in children]
    return node


PROJECT_MAP = _item("FolderItem", "???", "N-ROOT", [
    _item("ProjectItem", "House", "N-PRJ", [
        _item("UndefinedItem", "Stories", "N-STS", [
            _item("StoryItem", "Ground Floor", "N-ST0")]),
        _item("UndefinedItem", "Sections", "N-SECS", [
            _item("SectionItem", "Building Section", "N-SEC"),
            _item("SectionItem", "Other Section", "N-SEC2")]),
        _item("ScheduleItem", "Door Schedule", "N-SCH")])])

LAYOUT_BOOK = _item("UndefinedItem", "LAYOUT MAP ROOT", "N-LROOT", [
    _item("BookItem", "House", "N-BOOK", [
        _item("LayoutItem", "A-01 Plans", "N-LAY", [
            _item("DrawingItem", "Ground Floor", "N-DRW")]),
        _item("MasterFolderItem", "Masters", "N-MF", [
            _item("MasterLayoutItem", "A3 Landscape", "N-MAS")])])])

NAV_DB = {"N-ST0": FLOOR_PLAN_DB, "N-SEC": SEC_DB, "N-SEC2": SEC2_DB,
          "N-LAY": LAY_DB, "N-MAS": MAS_DB}


class Project:
    """A fake Tapir project: databases holding elements, a navigator, stories."""

    def __init__(self, databases: dict[str, dict[str, dict]], window="FloorPlan",
                 act_story=0, stories=None):
        # databases: db id -> {GUID: {"type": ..., "floorIndex": ...}}
        self.databases = databases
        self.window = window
        self.act_story = act_story
        self.stories = stories or [{"index": 0, "name": "Ground"},
                                   {"index": 1, "name": "First"},
                                   {"index": 2, "name": ""}]
        self.listed: list[str] = []

    def details(self, guid):
        for elements in self.databases.values():
            if guid.upper() in elements:
                return elements[guid.upper()]
        return None

    def get_all_elements(self, params):
        dbs = (params or {}).get("databases")
        db = dbs[0]["databaseId"]["guid"] if dbs else FLOOR_PLAN_DB
        self.listed.append(db)
        return {"elements": [{"elementId": {"guid": g}} for g in self.databases.get(db, {})],
                "executionResultForDatabases": [{"success": True}]}

    def get_details(self, params):
        out = []
        for e in params["elements"]:
            d = self.details(e["elementId"]["guid"])
            out.append(dict(d) if d is not None else
                       {"error": {"code": -2130313112, "message": "Element not found"}})
        return {"detailsOfElements": out}

    def core(self) -> FakeCore:
        tapir = dict(api_replays.TAPIR)
        tapir.update({
            "GetAllElements": self.get_all_elements,
            "GetDetailsOfElements": self.get_details,
            "GetNavigatorItemTree": lambda p: {"navigatorItemTree": {
                "ProjectMap": PROJECT_MAP, "LayoutBook": LAYOUT_BOOK}[p["navigatorMapId"]]},
            "GetDatabaseIdFromNavigatorItemId": lambda p: {"databases": [
                {"databaseId": {"guid": NAV_DB[i["navigatorItemId"]["guid"]]}}
                if i["navigatorItemId"]["guid"] in NAV_DB else
                {"error": {"code": 1, "message": "no database"}}
                for i in p["navigatorItemIds"]]},
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
            "GetStories": lambda p: {"firstStory": 0, "lastStory": 2,
                                     "actStory": self.act_story, "skipNullFloor": False,
                                     "stories": self.stories},
        })
        return FakeCore(official=dict(api_replays.OFFICIAL), tapir=tapir)


def wall(story):
    return {"type": "Wall", "floorIndex": story}


def conn_for(project: Project) -> tuple[ArchicadConnection, FakeCore]:
    core = project.core()
    return ArchicadConnection(19723, core=core), core


def walked(project: Project) -> list[str]:
    return [db for db in project.listed if db != FLOOR_PLAN_DB]


# ---------- locate ----------

def test_floor_plan_elements_get_their_story():
    project = Project({FLOOR_PLAN_DB: {"W0": wall(0), "W1": wall(1)}})
    conn, _ = conn_for(project)
    located = views.locate(conn, ["W0", "W1"], "FloorPlan")
    assert located.floor_plan == {"W0": 0, "W1": 1}
    assert located.elsewhere == {} and located.not_found == [] and located.not_searched == []
    assert walked(project) == []


def test_lower_case_and_repeated_guids_match_once():
    project = Project({FLOOR_PLAN_DB: {"ABC": wall(0)}})
    conn, _ = conn_for(project)
    located = views.locate(conn, ["abc", "ABC"], "FloorPlan")
    assert located.floor_plan == {"abc": 0}


def test_section_element_is_found_in_its_database():
    project = Project({FLOOR_PLAN_DB: {"W0": wall(0)},
                       SEC_DB: {"S1": {"type": "Object", "floorIndex": 0}}})
    conn, core = conn_for(project)
    located = views.locate(conn, ["S1"], "FloorPlan")
    view = located.elsewhere["S1"]
    assert (view.kind, view.name, view.navigator_item_id, view.database_id) == \
        ("SectionItem", "Building Section", "N-SEC", SEC_DB)
    asked = [i["navigatorItemId"]["guid"] for c, p in core.calls
             if c == "GetDatabaseIdFromNavigatorItemId" for i in p["navigatorItemIds"]]
    assert asked == ["N-SEC", "N-SEC2", "N-LAY", "N-MAS"]


def test_walk_stops_once_everything_is_placed():
    project = Project({FLOOR_PLAN_DB: {}, SEC_DB: {"S1": {"type": "Object", "floorIndex": 0}},
                       SEC2_DB: {"S2": {"type": "Object", "floorIndex": 0}}})
    conn, _ = conn_for(project)
    views.locate(conn, ["S1"], "FloorPlan")
    assert walked(project) == [SEC_DB]


def test_drawings_are_looked_for_in_layouts_first():
    project = Project({FLOOR_PLAN_DB: {}, LAY_DB: {"D1": {"type": "Drawing", "floorIndex": 0}}})
    conn, _ = conn_for(project)
    located = views.locate(conn, ["D1"], "FloorPlan")
    assert walked(project) == [LAY_DB]
    assert located.elsewhere["D1"].name == "A-01 Plans"


def test_unreadable_guid_skips_the_walk_from_the_floor_plan():
    project = Project({FLOOR_PLAN_DB: {"W0": wall(0)}})
    conn, core = conn_for(project)
    located = views.locate(conn, ["GONE"], "FloorPlan")
    assert located.not_found == ["GONE"]
    assert not any(c == "GetNavigatorItemTree" for c, _ in core.calls)


def test_from_a_layout_the_walk_runs_even_for_unreadable_guids():
    project = Project({FLOOR_PLAN_DB: {"W0": wall(0)}}, window="Layout")
    conn, core = conn_for(project)
    located = views.locate(conn, ["GONE"], "Layout")
    assert located.not_found == ["GONE"]
    assert walked(project) == [SEC_DB, SEC2_DB, LAY_DB, MAS_DB]


def test_walk_cap_leaves_the_rest_not_searched(monkeypatch):
    monkeypatch.setattr(views, "WALK_CAP", 1)
    project = Project({FLOOR_PLAN_DB: {}, SEC2_DB: {"S2": {"type": "Object", "floorIndex": 0}}})
    conn, _ = conn_for(project)
    located = views.locate(conn, ["S2"], "FloorPlan")
    assert located.not_searched == ["S2"]
    assert located.not_found == []


# ---------- choose ----------

SECTION = View("SectionItem", "Building Section", "N-SEC", SEC_DB)
OTHER = View("SectionItem", "Other Section", "N-SEC2", SEC2_DB)


def located(floor_plan=None, elsewhere=None) -> Located:
    elsewhere = elsewhere or {}
    order = list(dict.fromkeys(elsewhere.values()))
    return Located(floor_plan=floor_plan or {}, elsewhere=elsewhere, not_found=[],
                   not_searched=[], views=order)


def test_choose_picks_the_story_holding_most():
    view, story = views.choose(located({"A": 0, "B": 1, "C": 1}), "FloorPlan", 0)
    assert view.kind == "FloorPlan" and story == 1


def test_choose_tie_goes_to_the_active_story():
    assert views.choose(located({"A": 0, "B": 2}), "FloorPlan", 2)[1] == 2
    assert views.choose(located({"A": 1, "B": 2}), "FloorPlan", 0)[1] == 1


def test_choose_floor_plan_wins_a_tie_with_a_view():
    view, story = views.choose(located({"A": 0}, {"S": SECTION}), "Section", 0)
    assert view.kind == "FloorPlan" and story == 0


def test_choose_view_holding_most_wins():
    view, story = views.choose(
        located({"A": 0}, {"S": SECTION, "T": SECTION, "U": OTHER}), "FloorPlan", 0)
    assert view == SECTION and story is None


def test_choose_view_tie_goes_to_navigator_order():
    view, _ = views.choose(located({}, {"U": OTHER, "S": SECTION}), "FloorPlan", 0)
    assert view == OTHER  # located() keeps the order given, standing in for walk order


def test_choose_stays_in_3d_for_model_elements():
    view, story = views.choose(located({"A": 0, "B": 1}), "3DModel", 0)
    assert view.kind == "3DModel" and story is None


def test_choose_nothing_located():
    assert views.choose(located(), "FloorPlan", 0) == (None, None)
