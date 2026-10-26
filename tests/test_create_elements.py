"""create_elements: every element Tapir can create, checked before it is sent.

The fake below answers like Tapir 1.5.x: Create* commands return one entry per
item, either {"elementId": {"guid"}} or {"error": {"code", "message"}}.
"""
import math

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create import CREATE_TYPES, create_elements
from archicad_mcp.core.part_records import PartRecords
from archicad_mcp.gateway.registry import DEFINITIONS_DIR, _load_js_json, build_registry
from tests.conftest import FakeCore
from tests.fixtures import api_replays
from tests.fixtures.learned_parts import LEAVES, bed_record, door_record, store

WALL = {"begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 5, "y": 0},
        "height": 3.0, "thickness": 0.2}


# GUIDs must be UUID-shaped: the item schemas reject anything else.
W1, W2 = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000a2"
S1, GONE = "00000000-0000-4000-8000-0000000000b1", "00000000-0000-4000-8000-0000000000ff"


def door(host: str, **extra) -> dict:
    return {"ownerWallId": {"guid": host}, "centerOffset": 1.0, **extra}


def chair(name="Chair 01", **extra) -> dict:
    return {"libraryPartName": name, "coordinates": {"x": 0, "y": 0, "z": 0}, **extra}


SIZE = {"dimensions": {"x": 0.5, "y": 0.5, "z": 0.9}}


class Plan:
    """Element types by GUID, which elements Archicad would refuse, the favourites,
    the loaded library parts, the walls' begin and end points."""

    def __init__(self, types=None, refused=(), favorites=None, window="FloorPlan",
                 missing_commands=(), elsewhere=(), library=None, pictures=(),
                 unread=(), archicad=29, tapir_version="1.8.2", walls=None,
                 parameter_error=None, levels=(0.0, 3.0, 6.0), act_story=0,
                 wall_details=None):
        self.types = dict(types or {})
        # Story i is at levels[i]; the floor plan shows act_story (None: not said).
        self.levels = list(levels)
        self.act_story = act_story
        # GUID -> ((begin x, y), (end x, y)); each is a Wall. wall_details adds to a
        # wall's details (flipped, arcAngle).
        self.walls = dict(walls or {})
        self.wall_details = dict(wall_details or {})
        self.types.update({g: "Wall" for g in self.walls})
        # SetGDLParametersOfElements answers every element with this error when set.
        self.parameter_error = parameter_error
        self.parameter_writes: list[dict] = []
        self.elsewhere = set(elsewhere)  # exist, but in another view's database
        self.refused = set(refused)
        self.favorites = dict(favorites or {})
        self.window = window
        self.missing_commands = set(missing_commands)
        # Document names of the loaded parts; None leaves GetAvailableLibraryParts
        # unanswered. `unread` are the library indices ACAPI_LibraryPart_Get fails on.
        self.library = library
        self.pictures = list(pictures)
        self.unread = set(unread)
        self.archicad = archicad
        self.tapir_version = tapir_version
        self.created: list[tuple[str, dict]] = []
        self.create_errors: dict[int, dict] = {}

    def _library_parts(self, p):
        entries = [(n, "Object") for n in self.library] + [(n, "Picture") for n in self.pictures]
        indices = (i for i in range(1, len(entries) + len(self.unread) + 1)
                   if i not in self.unread)
        return {"libraryParts": [{"guid": f"lib-{index}", "index": index,
                                  "documentName": name, "fileName": f"{name}.gsm",
                                  "typeId": type_id}
                                 for (name, type_id), index in zip(entries, indices)],
                "skippedCount": len(self.unread),
                "skippedSample": [{"index": i, "code": -2130313112}
                                  for i in sorted(self.unread)[:5]]}

    def connection(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing_commands}
        official["API.GetProductInfo"] = {"version": self.archicad, "buildNumber": 5101}
        tapir = dict(api_replays.TAPIR)
        tapir["GetCurrentWindowType"] = lambda p: {"currentWindowType": self.window}
        tapir["GetAddOnVersion"] = {"version": self.tapir_version}
        # The active window's database: the other-database guard checks hosts
        # against this list (core/editability.py _elsewhere).
        tapir["GetAllElements"] = lambda p: {"elements": [
            {"elementId": {"guid": g}} for g in self.types if g not in self.elsewhere]}
        tapir["FilterElements"] = lambda p: {"elements": [
            e for e in p["elements"]
            if e["elementId"]["guid"] in self.types
            and e["elementId"]["guid"] not in self.refused]}
        tapir["GetDetailsOfElements"] = lambda p: {"detailsOfElements": [
            self._details(e["elementId"]["guid"])
            if e["elementId"]["guid"] in self.types
            else {"error": {"code": -2130313112, "message": "not found"}}
            for e in p["elements"]]}
        tapir["SetGDLParametersOfElements"] = self._set_parameters
        tapir["GetStories"] = lambda p: {
            "firstStory": 0, "lastStory": len(self.levels) - 1, "actStory": self.act_story,
            "skipNullFloor": False,
            "stories": [{"index": i, "floorId": i + 1, "level": level, "height": 3.0,
                         "name": f"Story {i}"} for i, level in enumerate(self.levels)]}
        tapir["GetFavoritesByType"] = lambda p: {
            "favorites": self.favorites.get(p["elementType"], [])}
        if self.library is not None:
            tapir["GetAvailableLibraryParts"] = self._library_parts
        official["API.GetTypesOfElements"] = lambda p: {"typesOfElements": [
            {"typeOfElement": {"elementId": e["elementId"],
                               "elementType": self.types[e["elementId"]["guid"]]}}
            if e["elementId"]["guid"] in self.types
            else {"error": {"code": 7204, "message": "Element not found"}}
            for e in p["elements"]]}
        for spec in CREATE_TYPES.values():
            tapir[spec.command] = self._creator(spec.command)
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def _details(self, guid):
        entry = {"type": self.types[guid], "floorIndex": 0, "layerIndex": 1}
        if guid in self.walls:
            (bx, by), (ex, ey) = self.walls[guid]
            entry["details"] = {"begCoordinate": {"x": bx, "y": by},
                                "endCoordinate": {"x": ex, "y": ey},
                                **self.wall_details.get(guid, {})}
        return entry

    def _set_parameters(self, p):
        self.parameter_writes.append(p)
        entries = p["elementsWithGDLParameters"]
        if self.parameter_error is not None:
            return {"executionResults": [{"success": False, "error": self.parameter_error}
                                         for _ in entries]}
        return {"executionResults": [{"success": True} for _ in entries]}

    def _creator(self, command):
        def create(p):
            [items] = p.values()
            self.created.append((command, p))
            return {"elements": [
                self.create_errors.get(i) or {"elementId": {"guid": f"new-{i}"}}
                for i in range(len(items))]}
        return create

    def sent(self, command: str) -> list[dict]:
        return [p for c, p in self.created if c == command]


def element_creators() -> set[str]:
    """Every bundled Tapir command that answers with a list of created elements."""
    groups = _load_js_json(DEFINITIONS_DIR / "command_definitions.js", "gCommands")
    return {c["name"] for g in groups for c in g["commands"]
            if ((c.get("outputScheme") or {}).get("properties") or {})
            .get("elements") == {"$ref": "#/ElementIdsOrErrors"}}


def test_every_tapir_command_that_creates_elements_has_a_type():
    covered = {spec.command for spec in CREATE_TYPES.values()}
    assert element_creators() - covered == set()


def test_plural_and_command_names_resolve_to_the_same_type():
    conn = Plan().connection()
    for name in ("wall", "walls", "Wall", "CreateWalls"):
        assert create_elements(conn, name, [WALL])["command"] == "CreateWalls"


def test_unknown_type_lists_the_valid_ones():
    result = create_elements(Plan().connection(), "gazebo", [{}])
    assert "gazebo" in result["error"]
    assert "wall" in result["valid_types"] and "door" in result["valid_types"]


def test_empty_items_returns_the_item_schema():
    result = create_elements(Plan().connection(), "wall", [])
    assert result["command"] == "CreateWalls"
    assert {"begCoordinate", "height", "thickness"} <= set(result["item_schema"]["properties"])


def test_an_invalid_item_is_reported_by_index_and_nothing_is_sent():
    plan = Plan()
    # endCoordinate is required in every Tapir release; height stopped being
    # required in 1.7 (the favourite or the tool default supplies it).
    bad = {k: v for k, v in WALL.items() if k != "endCoordinate"}
    result = create_elements(plan.connection(), "wall", [WALL, bad], dry_run=False)
    assert result["problems"][0]["index"] == 1
    assert "endCoordinate" in result["problems"][0]["message"]
    assert result.get("created", 0) == 0
    assert plan.sent("CreateWalls") == []


def test_a_door_needs_a_wall_host():
    plan = Plan(types={W1: "Wall", S1: "Slab"})
    result = create_elements(plan.connection(), "door",
                             [door(W1), door(S1)], dry_run=False)
    assert [s["index"] for s in result["skipped"]] == [1]
    assert "Slab" in result["skipped"][0]["reason"]
    [payload] = plan.sent("CreateDoors")
    assert payload == {"doorsData": [door(W1)]}
    assert result["by_index"] == {"0": "new-0"}


def test_a_host_archicad_would_refuse_is_skipped_with_the_reason():
    plan = Plan(types={W1: "Wall", W2: "Wall"}, refused={W2})
    result = create_elements(plan.connection(), "window", [door(W1), door(W2)])
    assert [s["index"] for s in result["skipped"]] == [1]
    assert "not editable" in result["skipped"][0]["reason"]
    assert result["payload"] == {"windowsData": [door(W1)]}


def test_a_missing_host_is_skipped():
    plan = Plan(types={W1: "Wall"})
    result = create_elements(plan.connection(), "opening",
                             [{"ownerElementId": {"guid": GONE},
                               "basePoint": {"x": 0, "y": 0, "z": 0}}])
    assert "not found" in result["skipped"][0]["reason"]
    assert result["ready"] == 0


def test_an_unknown_favourite_is_skipped_with_close_matches():
    plan = Plan(favorites={"Wall": ["EXT 300 Brick", "INT 100 Drywall"]})
    result = create_elements(plan.connection(), "wall",
                             [dict(WALL, favoriteName="EXT 300 Brik"),
                              dict(WALL, favoriteName="INT 100 Drywall")])
    assert [s["index"] for s in result["skipped"]] == [0]
    assert "EXT 300 Brick" in result["skipped"][0]["reason"]
    assert result["ready"] == 1


def test_full_success_keeps_the_original_result_shape():
    plan = Plan()
    result = create_elements(plan.connection(), "wall", [WALL, WALL], dry_run=False)
    assert result == {"dry_run": False, "created": 2, "elements": ["new-0", "new-1"]}


def test_tapir_errors_per_item_are_grouped_with_their_indices():
    plan = Plan()
    conn = plan.connection()
    plan.create_errors = {1: {"error": {"code": 7, "message": "bad"}},
                          2: {"error": {"code": 7, "message": "bad"}}}
    result = create_elements(conn, "wall", [WALL, WALL, WALL], dry_run=False)
    assert result["created"] == 1
    assert result["elements"] == ["new-0"]
    assert result["failed"] == [{"code": 7, "message": "bad", "count": 2,
                                 "indices": [1, 2]}]
    assert result["by_index"] == {"0": "new-0"}


def test_a_command_the_running_addon_lacks_is_refused_with_its_version():
    plan = Plan(missing_commands={"CreateMEPElements"})
    result = create_elements(plan.connection(), "mep_element",
                             [{"position": {"x": 0, "y": 0, "z": 0}}], dry_run=False)
    assert "CreateMEPElements" in result["error"]
    assert "1.5.6" in result["error"]
    assert plan.sent("CreateMEPElements") == []


def test_model_types_warn_when_the_floor_plan_is_not_active():
    result = create_elements(Plan(window="Layout").connection(), "wall", [WALL])
    assert result["active_window"] == "Layout"
    assert any("floor plan" in w for w in result["warnings"])
    assert result["ready"] == 1


def test_2d_types_do_not_warn_in_another_window():
    line = {"begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 1, "y": 0}}
    result = create_elements(Plan(window="Details").connection(), "line", [line])
    assert result["warnings"] == []


def test_model_types_do_not_warn_from_the_3d_window():
    # Live 19.10.2026 (AC 29, Tapir 1.5.10): a wall created with the 3D window
    # active landed on the floor plan; from a Section or Layout window Tapir
    # answered "Failed to create new Wall" for the item.
    result = create_elements(Plan(window="3DModel").connection(), "wall", [WALL])
    assert result["warnings"] == []


def test_a_fork_only_command_is_named_as_such_when_missing():
    # CreateRailings exists only in the local Tapir fork (local_commands.json),
    # so "update Tapir" would send a stock-Tapir user on a useless errand.
    plan = Plan(missing_commands={"CreateRailings"})
    result = create_elements(plan.connection(), "railing",
                             [{"referenceLinePoints": [{"x": 0, "y": 0, "z": 0},
                                                       {"x": 3, "y": 0, "z": 0}]}])
    assert "no Tapir release has yet" in result["error"]
    assert "install-tapir" not in result["error"]


def test_a_host_in_another_views_database_is_skipped():
    # IsEditable passes such a host from the floor plan, and Archicad then
    # quietly does nothing to it (live 19.10.2026); the guard catches it.
    plan = Plan(types={W1: "Wall", W2: "Wall"}, elsewhere={W2})
    result = create_elements(plan.connection(), "door", [door(W1), door(W2)])
    assert [s["index"] for s in result["skipped"]] == [1]
    assert "another view's database" in result["skipped"][0]["reason"]


# Library parts. Tapir finds the part by document name only when the item is
# created (ElementCreationCommands.cpp, ResolveLibraryPartName), so a misspelt
# name used to surface as "Not found library part" after commit.

def test_an_unknown_library_part_is_skipped_with_close_matches():
    plan = Plan(library=["Chair 01", "Chair 02", "Table 01"])
    result = create_elements(plan.connection(), "object",
                             [chair("Chiar 01", **SIZE), chair("Table 01", **SIZE)])
    assert [s["index"] for s in result["skipped"]] == [0]
    assert "Chair 01" in result["skipped"][0]["reason"]
    assert result["ready"] == 1


def test_an_unknown_library_part_is_not_sent_on_commit():
    plan = Plan(library=["Chair 01"])
    result = create_elements(plan.connection(), "object",
                             [chair("Chiar 01", **SIZE), chair(**SIZE)], dry_run=False)
    assert plan.sent("CreateObjects") == [{"objectsData": [chair(**SIZE)]}]
    assert [s["index"] for s in result["skipped"]] == [0]


def test_a_lamp_part_name_is_checked_too():
    plan = Plan(library=["Light"])
    result = create_elements(plan.connection(), "lamp", [chair("Lihgt", **SIZE)])
    assert [s["index"] for s in result["skipped"]] == [0]
    assert "Light" in result["skipped"][0]["reason"]


@pytest.mark.parametrize("spelling", ["chair 01", "Chair 01.gsm"])
def test_a_library_part_spelt_another_way_is_sent_with_a_warning(spelling):
    # Whether Archicad's name lookup ignores case is unverified, so the item
    # goes ahead and the warning gives the library's own spelling.
    plan = Plan(library=["Chair 01"])
    result = create_elements(plan.connection(), "object", [chair(spelling, **SIZE)])
    assert result["ready"] == 1
    assert any("'Chair 01'" in w for w in result["warnings"])


def test_a_picture_file_is_not_a_library_part():
    plan = Plan(library=["Chair 01"], pictures=["Oak bark"])
    result = create_elements(plan.connection(), "object", [chair("Oak bark", **SIZE)])
    assert [s["index"] for s in result["skipped"]] == [0]


def test_unread_entries_after_the_last_part_do_not_stop_the_check():
    # Live 21.10.2026 (AC 29, Tapir 1.7.1, MCP-Test): 7402 parts listed, and the
    # 123 entries ACAPI_LibraryPart_Get failed on were the indices after them.
    plan = Plan(library=["Chair 01", "Table 01"], unread=(3, 4))
    result = create_elements(plan.connection(), "object", [chair("Chiar 01", **SIZE)])
    assert [s["index"] for s in result["skipped"]] == [0]


def test_an_unread_entry_among_the_parts_turns_the_skip_into_a_warning():
    plan = Plan(library=["Chair 01", "Table 01"], unread=(2,))
    result = create_elements(plan.connection(), "object", [chair("Chiar 01", **SIZE)])
    assert result["skipped"] == []
    assert result["ready"] == 1
    assert any("Chiar 01" in w for w in result["warnings"])


def test_an_unreadable_library_lets_archicad_judge_the_name():
    plan = Plan()  # GetAvailableLibraryParts goes unanswered
    result = create_elements(plan.connection(), "object", [chair("Chiar 01", **SIZE)])
    assert result["skipped"] == []
    assert result["ready"] == 1


def test_items_without_a_library_part_do_not_list_the_library():
    # The listing is about 1.2 MB on MCP-Test.
    plan = Plan(library=["Chair 01"])
    create_elements(plan.connection(), "wall", [WALL])
    assert "GetAvailableLibraryParts" not in [command for command, _ in plan.core.calls]


def _door_fields() -> dict:
    schema = build_registry()["CreateDoors"].input_schema
    return schema["properties"]["doorsData"]["items"]["properties"]


@pytest.mark.skipif("libraryPartName" not in _door_fields(),
                    reason="doors take a libraryPartName from Tapir 1.7.2")
def test_a_door_part_name_is_checked_once_doors_take_one():
    plan = Plan(types={W1: "Wall"}, library=["Door 26"])
    result = create_elements(plan.connection(), "door",
                             [door(W1, libraryPartName="Dor 26")])
    assert [s["index"] for s in result["skipped"]] == [0]
    assert "Door 26" in result["skipped"][0]["reason"]


# Sizes and fields Archicad drops. CreateObjects starts every item from the tool's
# current settings (ACAPI_Element_GetDefaults) and swaps in the library part.

@pytest.mark.parametrize("element_type, tool", [("object", "Object tool"),
                                                ("lamp", "Lamp tool")])
def test_items_without_dimensions_warn_that_they_take_the_tool_size(element_type, tool):
    # Live 29.09.2026 (AC 27, Tapir 1.5.9): an object placed without dimensions
    # took the Object tool's A, B and height, not the library part's own.
    plan = Plan(library=["Chair 01"])
    result = create_elements(plan.connection(), element_type,
                             [chair(**SIZE), chair(), chair()])
    [warning] = result["warnings"]
    assert tool in warning
    assert "1, 2" in warning
    assert result["ready"] == 3


def test_an_item_from_a_favourite_takes_its_size_from_the_favourite():
    plan = Plan(library=["Chair 01"], favorites={"Object": ["Office chair"]})
    result = create_elements(plan.connection(), "object",
                             [chair(favoriteName="Office chair")])
    assert result["warnings"] == []


@pytest.mark.parametrize("field, value", [("offset", {"x": -1.2, "y": 0.0}),
                                          ("useFixedAngle", True)])
def test_fields_archicad_discards_are_warned_about(field, value):
    plan = Plan(library=["Chair 01"])
    result = create_elements(plan.connection(), "object",
                             [chair(**SIZE, **{field: value}), chair(**SIZE),
                              chair(**SIZE, **{field: value})])
    [warning] = result["warnings"]
    assert field in warning
    assert "0, 2" in warning
    assert result["ready"] == 3


def test_a_long_run_of_items_is_named_by_its_first_indices():
    plan = Plan(library=["Chair 01"])
    result = create_elements(plan.connection(), "object", [chair() for _ in range(25)])
    [warning] = result["warnings"]
    assert "0, 1, 2" in warning
    assert "15 more" in warning
    assert "24" not in warning


# Tapir 1.7.2 types.

CURTAIN_WALL = {"coordinates": [{"x": 0, "y": 0}, {"x": 4, "y": 0}]}


def test_a_curtain_wall_from_an_unknown_favourite_is_skipped():
    plan = Plan(favorites={"CurtainWall": ["CW Office 1200"]})
    result = create_elements(plan.connection(), "curtain_wall",
                             [dict(CURTAIN_WALL, favoriteName="CW Ofice 1200")])
    assert [s["index"] for s in result["skipped"]] == [0]
    assert "CW Office 1200" in result["skipped"][0]["reason"]


def test_a_curtain_wall_warns_when_the_floor_plan_is_not_active():
    result = create_elements(Plan(window="Section").connection(), "curtain_wall",
                             [CURTAIN_WALL])
    assert any("floor plan" in w for w in result["warnings"])


def test_a_level_dimension_on_a_missing_element_is_skipped():
    plan = Plan(types={S1: "Slab"})
    result = create_elements(plan.connection(), "level_dimension",
                             [{"position": {"x": 0, "y": 0}, "parentElementId": {"guid": S1}},
                              {"position": {"x": 1, "y": 0}, "parentElementId": {"guid": GONE}}])
    assert [s["index"] for s in result["skipped"]] == [1]
    assert result["ready"] == 1


# Types only some Archicad versions create. Live 21.10.2026: Tapir 1.7.2 registers
# CreateElectricalPanels on Archicad 29 too, and answers it there with "This
# command requires Archicad 30 or newer." only once sent.

PANEL = {"position": {"x": 0, "y": 0, "z": 1.2}}


def test_an_archicad_30_type_is_refused_on_archicad_29_before_sending():
    plan = Plan(archicad=29)
    result = create_elements(plan.connection(), "electrical_panel", [PANEL], dry_run=False)
    assert "Archicad 30" in result["error"] and "Archicad 29" in result["error"]
    assert plan.sent("CreateElectricalPanels") == []


def test_mep_routes_are_refused_before_archicad_28():
    plan = Plan(archicad=27)
    route = {"routeSegments": []}
    result = create_elements(plan.connection(), "mep_route", [route])
    assert "Archicad 28" in result["error"]


def test_an_archicad_30_type_goes_ahead_on_archicad_30():
    result = create_elements(Plan(archicad=30).connection(), "electrical_panel", [PANEL])
    assert result["ready"] == 1


def test_a_circuit_on_a_missing_panel_is_skipped():
    plan = Plan(types={W1: "ElectricalPanel"}, archicad=30)
    result = create_elements(plan.connection(), "electrical_circuit",
                             [{"electricalPanelId": {"guid": W1}},
                              {"electricalPanelId": {"guid": GONE}}])
    assert [s["index"] for s in result["skipped"]] == [1]
    assert result["ready"] == 1


def test_a_tapir_older_than_the_definitions_is_warned_about():
    # Items are checked against the bundled schemas; a field a newer Tapir added
    # passes the dry run and the older add-on refuses it when sent.
    result = create_elements(Plan(tapir_version="1.7.1").connection(), "wall", [WALL])
    assert any("1.7.1" in w for w in result["warnings"])
    assert result["ready"] == 1


def test_the_tapir_the_definitions_come_from_is_not_warned_about():
    from archicad_mcp.tapir_requirements import definitions_version
    result = create_elements(Plan(tapir_version=definitions_version()).connection(),
                             "wall", [WALL])
    assert result["warnings"] == []


@pytest.mark.parametrize("element_type, tool", [("door", "Door tool"), ("window", "Window tool")])
def test_an_opening_without_a_sill_height_warns_that_it_takes_the_tool_sill(element_type, tool):
    # Live 21.10.2026 (Tapir 1.7.2): a door placed by libraryPartName without
    # sillHeight came in with the Door tool's 0.1 m sill.
    plan = Plan(types={W1: "Wall"})
    result = create_elements(plan.connection(), element_type,
                             [door(W1), door(W1, sillHeight=0.0)])
    [warning] = [w for w in result["warnings"] if "sillHeight" in w]
    assert "item 0" in warning and tool in warning


def test_an_opening_from_a_favourite_takes_its_sill_from_the_favourite():
    plan = Plan(types={W1: "Wall"}, favorites={"Door": ["Entrance 1000"]})
    result = create_elements(plan.connection(), "door",
                             [door(W1, favoriteName="Entrance 1000")])
    assert result["warnings"] == []


# Placing a learned part by what the sketch shows. The fake library gives its parts
# the GUIDs lib-1, lib-2, ... in the order listed; a record must carry the same one
# (tests/fixtures/learned_parts.py).

# The opening's centre is door()'s centerOffset, 1.0 m along this wall from its begin.
WALL_ALONG_X = ((0.0, 0.0), (5.0, 0.0))


def bed(part="Bed Double 01", **placement) -> dict:
    """A bed 2 m long and 1.6 m wide centred on (2, 3), its head pointing +x."""
    shown = {"center": {"x": 2.0, "y": 3.0}, "size": {"length": 2.0, "width": 1.6, "height": 0.5},
             "head": {"x": 1.0, "y": 0.0}}
    shown.update(placement)
    return {"libraryPartName": part, "placement": shown}


def placed_door(hinge_x: float, toward_y: float, leaves: str | None = None, **extra) -> dict:
    """A door in W1, hinged at the jamb nearer x = hinge_x, opening toward y = toward_y."""
    placement = {"hinge_near": {"x": hinge_x, "y": 0.0}, "opens_toward": {"x": 1.0, "y": toward_y}}
    if leaves is not None:
        placement["leaves"] = leaves
    return door(W1, libraryPartName="Door", sillHeight=0.0, placement=placement, **extra)


def test_a_bed_by_footprint_becomes_tapir_fields(tmp_path):
    plan = Plan(library=["Bed Double 01"])
    conn = plan.connection()
    records = store(tmp_path, bed_record())

    dry = create_elements(conn, "object", [bed()], records=records)

    [item] = dry["payload"]["objectsData"]
    assert "placement" not in item
    assert item["libraryPartName"] == "Bed Double 01"
    # The head (local +y) turns to plan +x: three quarter turns counter-clockwise.
    assert item["angle"] == pytest.approx(3 * math.pi / 2)
    # The foot end sits at the insertion point, half the length west of the centre.
    assert item["coordinates"] == pytest.approx({"x": 1.0, "y": 3.0, "z": 0.0})
    assert item["reflected"] is False
    # Length runs along the head (B for a +y head), width across it (A).
    assert item["dimensions"] == {"x": 1.6, "y": 2.0, "z": 0.5}
    assert item["floorIndex"] == 0
    derived = {k: item[k]
               for k in ("coordinates", "angle", "reflected", "dimensions", "floorIndex")}
    assert dry["placements"] == [{"index": 0, "part": "Bed Double 01", "derived": derived}]
    assert dry["skipped"] == []
    assert not any("No dimensions" in w for w in dry["warnings"])

    committed = create_elements(conn, "object", [bed()], dry_run=False, records=records)
    assert plan.sent("CreateObjects") == [{"objectsData": [item]}]
    assert committed["created"] == 1
    assert committed["placements"] == dry["placements"]


def test_a_mirrored_bed_says_so(tmp_path):
    plan = Plan(library=["Bed Double 01"])
    result = create_elements(plan.connection(), "object", [bed(mirrored=True)],
                             records=store(tmp_path, bed_record()))
    [item] = result["payload"]["objectsData"]
    assert item["reflected"] is True


def test_placement_without_a_record_is_skipped_with_the_next_step(tmp_path):
    plan = Plan(library=["Bed Double 01", "Chair 01"])
    records = store(tmp_path, bed_record(name="Chair 01", guid="lib-2", head=None))
    result = create_elements(plan.connection(), "object", [bed(), bed("Chair 01")],
                             dry_run=False, records=records)
    never, unfinished = result["skipped"]
    assert never["index"] == 0
    assert "'Bed Double 01' has not been learned" in never["reason"]
    assert "learn_library_part" in never["reason"]
    assert unfinished["index"] == 1
    assert "head" in unfinished["reason"] and "record_library_part" in unfinished["reason"]
    assert result["created"] == 0
    assert plan.sent("CreateObjects") == []


def test_a_record_for_another_guid_is_skipped(tmp_path):
    plan = Plan(library=["Bed Double 01"])
    records = store(tmp_path, bed_record(guid="6B4F1A52-0C3E-4D1B-9A77-2E5C8D90F311"))
    result = create_elements(plan.connection(), "object", [bed()], records=records)
    [skip] = result["skipped"]
    assert "6B4F1A52-0C3E-4D1B-9A77-2E5C8D90F311" in skip["reason"]
    assert "lib-1" in skip["reason"]
    assert "learn_library_part" in skip["reason"]
    assert result["ready"] == 0
    assert "placements" not in result


def test_a_learned_part_that_is_not_loaded_is_skipped_with_close_names(tmp_path):
    plan = Plan(library=["Bed Double 02"])
    result = create_elements(plan.connection(), "object", [bed()],
                             records=store(tmp_path, bed_record()))
    [skip] = result["skipped"]
    assert "no loaded library part named 'Bed Double 01'" in skip["reason"]
    assert "Bed Double 02" in skip["reason"]


def test_an_unlisted_library_cannot_confirm_the_record(tmp_path):
    plan = Plan()  # GetAvailableLibraryParts goes unanswered
    result = create_elements(plan.connection(), "object", [bed()],
                             records=store(tmp_path, bed_record()))
    [skip] = result["skipped"]
    assert "could not be listed" in skip["reason"]


def test_placement_and_coordinates_together_are_skipped(tmp_path):
    plan = Plan(library=["Bed Double 01", "Door"], walls={W1: WALL_ALONG_X})
    conn = plan.connection()
    records = store(tmp_path, bed_record())
    objects = create_elements(conn, "object",
                              [dict(bed(), coordinates={"x": 0, "y": 0, "z": 0}),
                               dict(bed(), angle=0.0, dimensions={"x": 1, "y": 2, "z": 0.5})],
                              records=records)
    assert [s["reason"] for s in objects["skipped"]] == [
        "placement sets coordinates; leave one out",
        "placement sets angle and dimensions; leave one out"]
    doors = create_elements(conn, "door", [placed_door(1.4, 2.0, oSide=True)],
                            records=store(tmp_path, door_record(guid="lib-2")))
    assert [s["reason"] for s in doors["skipped"]] == ["placement sets oSide; leave one out"]


def test_a_malformed_placement_is_skipped_with_where_it_goes_wrong(tmp_path):
    plan = Plan(library=["Bed Double 01"])
    result = create_elements(plan.connection(), "object",
                             [bed(size={"length": 2.0}), bed(heading={"x": 1, "y": 0})],
                             records=store(tmp_path, bed_record()))
    missing_width, unknown_key = result["skipped"]
    assert "placement/size" in missing_width["reason"] and "width" in missing_width["reason"]
    assert "heading" in unknown_key["reason"]
    assert "placement_schema" in missing_width["reason"]


def test_placement_needs_a_library_part_name(tmp_path):
    plan = Plan(library=["Bed Double 01"])
    item = bed()
    del item["libraryPartName"]
    result = create_elements(plan.connection(), "object", [item],
                             records=store(tmp_path, bed_record()))
    [skip] = result["skipped"]
    assert "libraryPartName" in skip["reason"]
    assert "problems" not in result


def test_object_without_height_falls_back_to_the_record_then_skips(tmp_path):
    plan = Plan(library=["Bed Double 01", "Chair 01"])
    records = store(tmp_path, bed_record(height=0.45),
                    bed_record(name="Chair 01", guid="lib-2"))
    no_height = {"length": 2.0, "width": 1.6}
    result = create_elements(plan.connection(), "object",
                             [bed(size=no_height), bed("Chair 01", size=no_height)],
                             records=records)
    [item] = result["payload"]["objectsData"]
    assert item["dimensions"] == {"x": 1.6, "y": 2.0, "z": 0.45}
    [skip] = result["skipped"]
    assert skip["index"] == 1
    assert skip["reason"] == ("give size.height in metres: Tapir 1.7.2 cannot read a "
                              "part's own height")


def test_a_footprint_of_no_width_is_skipped_and_one_off_the_size_asked_warns(tmp_path):
    plan = Plan(library=["Bed Double 01", "Chair 01"])
    # A headboard 0.1 m past B; Chair 01 fitted against its read-back (2A - 1 wide).
    headboard = bed_record(footprint={"xMin": [-0.5, 0], "xMax": [0.5, 0],
                                      "yMin": [0, 0], "yMax": [1, 0.1]})
    flat = bed_record(name="Chair 01", guid="lib-2",
                      footprint={"xMin": [-1, 0.5], "xMax": [1, -0.5],
                                 "yMin": [-1, 0.5], "yMax": [1, -0.5]})
    small = {"length": 0.5, "width": 0.5, "height": 0.9}
    result = create_elements(plan.connection(), "object", [bed(), bed("Chair 01", size=small)],
                             records=store(tmp_path, headboard, flat))
    [skip] = result["skipped"]
    assert skip["index"] == 1
    assert "'Chair 01' covers 0.00 x 0.00 m at A 0.5, B 0.5" in skip["reason"]
    [warning] = [w for w in result["warnings"] if w.startswith("Item 0")]
    assert warning.startswith("Item 0 ('Bed Double 01') covers 2.10 m along its head and "
                              "1.60 m across")
    assert result["ready"] == 1


def test_a_part_whose_plan_size_does_not_follow_its_size_is_placed_by_coordinates(tmp_path):
    plan = Plan(library=["Chair 01"])
    records = store(tmp_path, bed_record(name="Chair 01", footprint=None))
    result = create_elements(plan.connection(), "object", [bed("Chair 01")], records=records)
    [skip] = result["skipped"]
    assert "plan size does not follow its own A and B" in skip["reason"]
    assert "coordinates, angle, reflected and dimensions" in skip["reason"]


def test_a_door_by_hinge_point_and_swing_gets_reflected_and_oside(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    records = store(tmp_path, door_record())
    result = create_elements(plan.connection(), "door",
                             [placed_door(1.4, 2.0), placed_door(0.6, 2.0),
                              placed_door(0.6, -2.0)],
                             records=records)
    first, second, third = result["payload"]["doorsData"]
    # Hinged past the centre (the end jamb), opening to the left of the wall: ft.
    assert first == door(W1, libraryPartName="Door", sillHeight=0.0,
                         reflected=False, oSide=True)
    # The begin jamb, opening left: tt.
    assert (second["reflected"], second["oSide"]) == (True, True)
    # The begin jamb, opening right: ff.
    assert (third["reflected"], third["oSide"]) == (False, False)
    # The hinge and swing read from the two points, in words.
    assert result["placements"][0] == {"index": 0, "part": "Door", "hinge": "end",
                                       "swing": "left",
                                       "derived": {"reflected": False, "oSide": True}}
    assert [(p["hinge"], p["swing"]) for p in result["placements"][1:]] == [
        ("begin", "left"), ("begin", "right")]
    walls_read = [p for c, p in plan.core.calls if c == "GetDetailsOfElements"
                  and p["elements"] == [{"elementId": {"guid": W1}}]]
    assert walls_read
    # A door stands on its wall's story: no story is read for it.
    assert "GetStories" not in [command for command, _ in plan.core.calls]


def test_a_door_sent_with_ref_side_true_and_a_placement_is_skipped(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    result = create_elements(plan.connection(), "door",
                             [placed_door(1.4, 2.0, refSide=True),
                              placed_door(1.4, 2.0, refSide=False)],
                             records=store(tmp_path, door_record()))
    [skip] = result["skipped"]
    assert skip["index"] == 0
    assert "refSide true" in skip["reason"] and "refSide false" in skip["reason"]
    assert result["payload"]["doorsData"][0]["refSide"] is False


def test_a_door_in_a_wall_flipped_otherwise_than_where_it_was_learned_warns(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X, W2: WALL_ALONG_X}, library=["Door"],
                wall_details={W1: {"flipped": False}, W2: {"flipped": True}})
    other_wall = placed_door(1.4, 2.0)
    other_wall["ownerWallId"] = {"guid": W2}
    result = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0), other_wall],
                             records=store(tmp_path, door_record(wall_flipped=True)))
    [warning] = [w for w in result["warnings"] if "flipped" in w]
    assert warning.startswith("Item 0 ('Door') stands in a wall with flipped false")
    assert "learned in a wall with flipped true" in warning and "capture_window" in warning
    # A record learned before the wall's flipped was kept warns of nothing.
    unknown = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0)],
                              records=store(tmp_path / "old", door_record()))
    assert not any("flipped" in w for w in unknown["warnings"])


def test_a_door_in_a_curved_wall_is_skipped(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"],
                wall_details={W1: {"arcAngle": 0.8}})
    result = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0)],
                             records=store(tmp_path, door_record()))
    [skip] = result["skipped"]
    assert f"wall {W1} is curved" in skip["reason"]
    assert "reflected and oSide" in skip["reason"]


@pytest.mark.parametrize("hinge_x, toward_y, words", [(1.02, 2.0, "hinge_near"),
                                                      (1.4, 0.01, "opens_toward")])
def test_a_door_whose_points_name_no_jamb_or_side_is_skipped(tmp_path, hinge_x, toward_y,
                                                             words):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    result = create_elements(plan.connection(), "door", [placed_door(hinge_x, toward_y)],
                             records=store(tmp_path, door_record()))
    [skip] = result["skipped"]
    assert words in skip["reason"]


def test_a_record_without_combos_says_hidden_opening_lines_need_showing(tmp_path):
    # Live 22.10.2026: MCP-Test's plan drew no window opening lines, so no combos could
    # be recorded; the user has to show them before learning again.
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    result = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0)],
                             records=store(tmp_path, door_record(combos=None)))
    [skip] = result["skipped"]
    assert "lacks combos" in skip["reason"]
    assert "no opening lines" in skip["reason"] and "ask the user to show them" in skip["reason"]
    assert "learn it again" in skip["reason"]


def test_a_door_in_a_wall_that_cannot_be_read_is_skipped(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    item = placed_door(1.4, 2.0)
    item["ownerWallId"] = {"guid": GONE}
    result = create_elements(plan.connection(), "door", [item, placed_door(1.4, 2.0)],
                             records=store(tmp_path, door_record()))
    [skip] = result["skipped"]
    assert skip["index"] == 0 and GONE in skip["reason"]
    assert result["ready"] == 1


def test_an_unknown_leaf_state_is_skipped_with_the_recorded_ones(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    records = store(tmp_path, door_record(leaves=2, leaf_states=LEAVES))
    result = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0, "both open")],
                             records=records)
    [skip] = result["skipped"]
    assert "'both open'" in skip["reason"] and "left open" in skip["reason"]


def test_leaves_are_written_after_the_create(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    conn = plan.connection()
    records = store(tmp_path, door_record(leaves=2, leaf_states=LEAVES))
    item = placed_door(1.4, 2.0, "left open")

    dry = create_elements(conn, "door", [item], records=records)
    assert dry["placements"][0]["derived"]["leaves"] == {"state": "left open",
                                                         "parameters": LEAVES["left open"]}
    assert "leaves" not in dry["payload"]["doorsData"][0]
    assert plan.parameter_writes == []

    result = create_elements(conn, "door", [item], dry_run=False, records=records)
    assert plan.parameter_writes == [{"elementsWithGDLParameters": [
        {"elementId": {"guid": "new-0"},
         "gdlParameters": [{"name": "gs_leaf_1_open", "value": 1},
                           {"name": "gs_leaf_2_open", "value": 0}]}]}]
    commands = [command for command, _ in plan.core.calls]
    assert commands.index("SetGDLParametersOfElements") > commands.index("CreateDoors")
    assert result["created"] == 1
    assert "placed_not_set" not in result


@pytest.mark.parametrize("answer, message", [
    ("refused", "Failed to change the parameters"),
    ("raises", "no canned response for SetGDLParametersOfElements"),
])
def test_a_refused_leaves_write_keeps_the_door_and_reports_it(tmp_path, answer, message):
    error = {"code": -2130313215, "message": "Failed to change the parameters"}
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"],
                parameter_error=error if answer == "refused" else None)
    conn = plan.connection()
    if answer == "raises":
        del plan.core.tapir_responses["SetGDLParametersOfElements"]
    records = store(tmp_path, door_record(leaves=2, leaf_states=LEAVES))
    result = create_elements(conn, "door", [placed_door(1.4, 2.0, "left open")],
                             dry_run=False, records=records)
    assert result["created"] == 1
    assert result["elements"] == ["new-0"]
    [unset] = result["placed_not_set"]
    assert unset["index"] == 0 and unset["guid"] == "new-0"
    assert unset["leaves"] == "left open"
    assert unset["parameters"] == LEAVES["left open"]
    assert message in unset["message"]
    assert "DeleteElements" not in [command for command, _ in plan.core.calls]


@pytest.mark.parametrize("element_type, tool", [("door", "Door tool"), ("window", "Window tool")])
def test_doors_by_part_name_warn_that_the_tool_now_holds_it(element_type, tool):
    # Live 22.10.2026 (Tapir 1.7.2): CreateDoors and CreateWindows set the tool to the
    # part named and never set it back.
    plan = Plan(types={W1: "Wall"}, library=["Door 26", "Door 27"])
    conn = plan.connection()
    items = [door(W1, libraryPartName="Door 26", sillHeight=0.0),
             door(W1, libraryPartName="Door 27", sillHeight=0.0)]
    dry = create_elements(conn, element_type, items)
    [warning] = [w for w in dry["warnings"] if "will hold" in w]
    assert f"the {tool} will hold 'Door 27'" in warning
    committed = create_elements(conn, element_type, items, dry_run=False)
    [warning] = [w for w in committed["warnings"] if "now holds" in w]
    assert f"the {tool} now holds 'Door 27'" in warning
    plain = create_elements(conn, element_type, [door(W1, sillHeight=0.0)])
    assert plain["warnings"] == []


@pytest.mark.parametrize("element_type, tool, favorite_type",
                         [("door", "Door tool", "Door"), ("window", "Window tool", "Window")])
def test_doors_by_part_name_warn_that_their_parameters_are_out_of_reach(element_type, tool,
                                                                         favorite_type):
    # Live 23.10.2026 (Tapir 1.7.2): an opening placed by libraryPartName gets a GDL
    # parameter list Archicad will not open, and the tool keeps it, so later openings
    # from the tool, hand-placed ones too, have the same fault. The same part placed
    # from a favourite read and wrote all its parameters.
    plan = Plan(types={W1: "Wall"}, library=["Door"],
                favorites={favorite_type: ["Generic", "Internal"]})
    items = [door(W1, libraryPartName="Door", sillHeight=0.0),
             door(W1, favoriteName="Generic", sillHeight=0.0)]
    result = create_elements(plan.connection(), element_type, items)
    [warning] = [w for w in result["warnings"] if "favoriteName" in w]
    assert warning.startswith("item 0:")
    assert "can be neither read nor written" in warning
    assert f"every later {element_type} from the {tool}" in warning
    assert f"the {favorite_type} favourites here: Generic, Internal" in warning
    from_favorite = create_elements(plan.connection(), element_type, items[1:])
    assert not [w for w in from_favorite["warnings"] if "neither read nor written" in w]


def test_the_parameter_warning_says_when_there_is_no_favourite_to_send():
    plan = Plan(types={W1: "Wall"}, library=["Door"], favorites={})
    result = create_elements(plan.connection(), "door",
                             [door(W1, libraryPartName="Door", sillHeight=0.0)])
    [warning] = [w for w in result["warnings"] if "neither read nor written" in w]
    assert "Send favoriteName instead" in warning
    assert "no Door favourites are saved" in warning and "favourites here" not in warning


def test_a_placed_door_warns_about_the_tool_too(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    result = create_elements(plan.connection(), "door", [placed_door(1.4, 2.0)],
                             records=store(tmp_path, door_record()))
    assert any("the Door tool will hold 'Door'" in w for w in result["warnings"])


def test_items_without_placement_are_unchanged(tmp_path):
    # The store is never read without a placement: a broken file stops nothing.
    broken = tmp_path / "library-parts.json"
    broken.write_text("not json", encoding="utf-8")
    records = PartRecords(broken)
    plan = Plan(types={W1: "Wall"}, library=["Chair 01"])
    conn = plan.connection()
    with_store = create_elements(conn, "object", [chair(**SIZE)], records=records)
    assert with_store == create_elements(conn, "object", [chair(**SIZE)])
    assert with_store["payload"] == {"objectsData": [chair(**SIZE)]}
    assert "placements" not in with_store
    committed = create_elements(conn, "door", [door(W1, sillHeight=0.0)], dry_run=False,
                                records=records)
    assert committed == {"dry_run": False, "created": 1, "elements": ["new-0"]}
    commands = [command for command, _ in plan.core.calls]
    assert "SetGDLParametersOfElements" not in commands
    assert "GetStories" not in commands


def test_placement_and_the_name_check_share_one_library_listing(tmp_path):
    plan = Plan(library=["Bed Double 01", "Chair 01"])
    create_elements(plan.connection(), "object", [bed(), chair(**SIZE)],
                    records=store(tmp_path, bed_record()))
    listings = [command for command, _ in plan.core.calls
                if command == "GetAvailableLibraryParts"]
    assert listings == ["GetAvailableLibraryParts"]


def test_placement_on_another_type_is_a_schema_problem(tmp_path):
    plan = Plan()
    result = create_elements(plan.connection(), "wall", [dict(WALL, placement={})],
                             records=store(tmp_path))
    assert "placement" in result["problems"][0]["message"]


def test_empty_items_show_the_placement_schema():
    conn = Plan().connection()
    objects = create_elements(conn, "object", [])["placement_schema"]
    assert set(objects["properties"]) == {"center", "size", "head", "mirrored"}
    assert set(objects["properties"]["size"]["properties"]) == {"length", "width", "height"}
    assert create_elements(conn, "lamp", [])["placement_schema"] == objects
    openings = create_elements(conn, "door", [])["placement_schema"]
    assert set(openings["properties"]) == {"hinge_near", "opens_toward", "leaves"}
    assert create_elements(conn, "window", [])["placement_schema"] == openings
    assert "placement_schema" not in create_elements(conn, "wall", [])


def test_a_schema_problem_reply_keeps_the_placement_skips(tmp_path):
    plan = Plan(library=["Bed Double 01", "Chair 01"])
    result = create_elements(plan.connection(), "object",
                             [bed(), chair(**SIZE, colour="red")], dry_run=False,
                             records=store(tmp_path))
    assert [p["index"] for p in result["problems"]] == [1]
    assert [s["index"] for s in result["skipped"]] == [0]
    assert plan.sent("CreateObjects") == []


def test_a_placed_object_goes_on_its_storys_floor(tmp_path):
    # Without floorIndex Tapir takes the story from an absolute z: z 0 on the ground
    # floor whatever story the plan shows. center.z is above the story's floor.
    plan = Plan(library=["Bed Double 01"], act_story=1)
    result = create_elements(plan.connection(), "object",
                             [bed(), dict(bed(center={"x": 2.0, "y": 3.0, "z": 0.4}),
                                          floorIndex=2)],
                             records=store(tmp_path, bed_record()))
    on_screen, upstairs = result["payload"]["objectsData"]
    assert on_screen["floorIndex"] == 1
    assert on_screen["coordinates"]["z"] == pytest.approx(3.0)
    assert upstairs["floorIndex"] == 2
    assert upstairs["coordinates"]["z"] == pytest.approx(6.4)
    assert result["placements"][0]["derived"]["floorIndex"] == 1
    stories_read = [command for command, _ in plan.core.calls if command == "GetStories"]
    assert stories_read == ["GetStories"]


@pytest.mark.parametrize("case, words", [
    ("unanswered", "stories could not be read"),
    ("no such story", "story 5"),
    ("story not said", "which story the floor plan shows"),
])
def test_an_object_whose_story_is_not_known_is_skipped(tmp_path, case, words):
    plan = Plan(library=["Bed Double 01"], act_story=None if case == "story not said" else 0)
    conn = plan.connection()
    if case == "unanswered":
        del plan.core.tapir_responses["GetStories"]
    item = dict(bed(), floorIndex=5) if case == "no such story" else bed()
    result = create_elements(conn, "object", [item], records=store(tmp_path, bed_record()))
    [skip] = result["skipped"]
    assert words in skip["reason"]
    assert result["ready"] == 0


def test_an_error_during_a_leaves_write_keeps_the_created_elements(tmp_path):
    plan = Plan(walls={W1: WALL_ALONG_X}, library=["Door"])
    conn = plan.connection()

    def broken(p):
        raise RuntimeError("socket closed")

    plan.core.tapir_responses["SetGDLParametersOfElements"] = broken
    records = store(tmp_path, door_record(leaves=2, leaf_states=LEAVES))
    result = create_elements(conn, "door", [placed_door(1.4, 2.0, "left open")],
                             dry_run=False, records=records)
    assert result["elements"] == ["new-0"]
    [unset] = result["placed_not_set"]
    assert "RuntimeError" in unset["message"] and "socket closed" in unset["message"]


# Heights Tapir reads as absolute. Live 23.10.2026 (AC 29, Tapir 1.7.2, Test.pln, the
# basement at -2.80): a column with z 0 and floorIndex -1 stood from 0.00 to 2.60, not
# on the story; a slab's level, a beam's and a stair's zCoordinate were absolute too.
# A wall's zCoordinate is the offset above floorIndex, as Tapir's schema says.

SQUARE = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]
BASEMENT = (-2.8, 0.0)  # story 0 of the fake at -2.80, story 1 at 0.00


def column(z, floor=0):
    return {"coordinates": {"x": 0, "y": 0, "z": z}, "floorIndex": floor}


def absolute_warnings(result):
    return [w for w in result["warnings"] if "absolute" in w]


def test_a_column_z_outside_the_story_it_names_warns_that_z_is_absolute():
    result = create_elements(Plan(levels=BASEMENT).connection(), "column",
                             [column(-2.8), column(0.5)])
    [warning] = absolute_warnings(result)
    assert "item 1" in warning and "item 0" not in warning
    assert "-2.80" in warning  # the story level the z has to build on


def test_a_slab_level_at_the_top_of_its_story_is_not_warned():
    slab = {"level": 0.2, "floorIndex": 0, "polygonCoordinates": SQUARE}
    result = create_elements(Plan(levels=BASEMENT).connection(), "slab", [slab])
    assert absolute_warnings(result) == []


@pytest.mark.parametrize("element_type, item", [
    ("beam", {"begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 3, "y": 0},
              "zCoordinate": 3.5, "floorIndex": 0}),
    ("stair", {"baseLinePoints": [{"x": 0, "y": 0}, {"x": 4, "y": 0}],
               "zCoordinate": 0.5, "floorIndex": 0}),
])
def test_beam_and_stair_heights_are_checked_as_absolute(element_type, item):
    result = create_elements(Plan(levels=BASEMENT).connection(), element_type, [item])
    [warning] = absolute_warnings(result)
    assert "item 0" in warning


def test_a_wall_z_is_an_offset_above_its_story_and_is_not_warned():
    wall = dict(WALL, floorIndex=0, zCoordinate=0.5)
    result = create_elements(Plan(levels=BASEMENT).connection(), "wall", [wall])
    assert absolute_warnings(result) == []


def test_the_item_schema_reply_says_a_column_z_is_absolute():
    result = create_elements(Plan().connection(), "column", [])
    assert any("absolute" in note for note in result["notes"])


# Stairs. Live 23.10.2026 (AC 29, Tapir 1.7.2): baseLinePoints is the stair's left
# edge and the flight lies flightWidth to its right. A U given as its centre line
# made the flights overlap, and Archicad answered only "Failed to create new Stair";
# the same U as its outer edge was created, but Archicad laid the risers out at its
# own going and the top flight ran 1.65 m past the baseline's end.

U_AS_CENTRE_LINE = [(7.3, 4.82), (5.615, 4.82), (5.615, 5.92), (7.3, 5.92)]
U_AS_OUTER_EDGE = [(7.3, 4.39), (5.19, 4.39), (5.19, 6.4), (7.3, 6.4)]


def stair(points, width=0.86):
    return {"baseLinePoints": [{"x": x, "y": y} for x, y in points], "zCoordinate": 0.0,
            "floorIndex": 0, "flightWidth": width}


def boxes(*extents):
    return {"boundingBoxes3D": [{"boundingBox3D": dict(zip(
        ("xMin", "xMax", "yMin", "yMax", "zMin", "zMax"), extent))} for extent in extents]}


def test_a_stair_whose_flights_overlap_is_warned_before_it_is_sent():
    result = create_elements(Plan().connection(), "stair",
                             [stair(U_AS_OUTER_EDGE), stair(U_AS_CENTRE_LINE)])
    [warning] = [w for w in result["warnings"] if "overlap" in w]
    assert "item 1" in warning and "item 0" not in warning
    assert "left edge" in warning


def test_a_stair_that_runs_past_its_baseline_is_reported_after_the_create():
    plan = Plan()
    conn = plan.connection()
    plan.core.tapir_responses["Get3DBoundingBoxes"] = boxes((5.19, 8.95, 4.39, 6.4, -0.15, 2.8))
    result = create_elements(conn, "stair", [stair(U_AS_OUTER_EDGE)], dry_run=False)
    [warning] = [w for w in result["warnings"] if "past" in w]
    assert "item 0" in warning and "1.65" in warning


def test_a_stair_within_its_baseline_and_flights_is_not_reported():
    plan = Plan()
    conn = plan.connection()
    plan.core.tapir_responses["Get3DBoundingBoxes"] = boxes((5.19, 7.4, 4.39, 6.4, -0.15, 2.8))
    result = create_elements(conn, "stair", [stair(U_AS_OUTER_EDGE)], dry_run=False)
    assert "warnings" not in result


def test_the_stair_schema_reply_says_the_baseline_is_the_left_edge():
    result = create_elements(Plan().connection(), "stair", [])
    assert any("left edge" in note for note in result["notes"])


# Sides. Live 23.10.2026 (AC 29, Tapir 1.7.2, Test.pln): composite walls drawn
# clockwise with referenceLineLocation Outside stood outside the outline; doors with
# oSide false swung to the right of their wall's direction, and flipping oSide also
# moved the hinge to the other jamb; windows with oSide false had their outer sill on
# the right.

@pytest.mark.parametrize("element_type, words", [
    ("wall", ("Outside", "left", "counter-clockwise")),
    ("door", ("oSide", "right", "reflected")),
    ("window", ("oSide", "right", "outside")),
])
def test_the_schema_reply_says_which_side_things_go(element_type, words):
    notes = " ".join(create_elements(Plan().connection(), element_type, [])["notes"])
    assert all(word in notes for word in words)


# Element IDs. Live 23.10.2026 (AC 29, Tapir 1.7.2, Test.pln): 14 doors all came in as
# 'DOO - 004', 8 windows as 'WD - 002' and 12 zones as 'ZON - 003', while walls,
# columns and slabs were numbered one by one.

def ids_read_as(plan, *ids):
    plan.core.official_responses["API.GetPropertyValuesOfElements"] = lambda p: {
        "propertyValuesForElements": [
            {"propertyValues": [{"propertyValue": {"type": "string", "status": "normal",
                                                   "value": value}}]}
            for _, value in zip(p["elements"], ids)]}


def test_created_elements_that_share_an_id_are_reported():
    plan = Plan(types={W1: "Wall"})
    conn = plan.connection()
    ids_read_as(plan, "DOO - 004", "DOO - 004", "DOO - 005")
    items = [door(W1, sillHeight=0), door(W1, sillHeight=0, centerOffset=2.0),
             door(W1, sillHeight=0, centerOffset=3.0)]
    result = create_elements(conn, "door", items, dry_run=False)
    [warning] = [w for w in result["warnings"] if "DOO - 004" in w]
    assert "items 0, 1" in warning and "General_ElementID" in warning


def test_created_elements_with_their_own_ids_are_not_reported():
    plan = Plan()
    conn = plan.connection()
    ids_read_as(plan, "SW - 006", "SW - 007")
    result = create_elements(conn, "wall", [WALL, WALL], dry_run=False)
    assert "warnings" not in result


def test_the_zone_schema_reply_says_stamps_follow_the_model_view_options():
    """Live 23.10.2026 (AC 29, Test.pln): 12 zones from CreateZones showed no stamp, in
    the app or a saved picture, even after UpdateZones. The office template's Model
    View Options hide zone stamps (zones are labelled with the Label tool); switched
    on, every stamp was there, its 667 parameters unchanged."""
    notes = " ".join(create_elements(Plan().connection(), "zone", [])["notes"])
    assert "Model View Options" in notes and "Label" in notes


def test_an_element_id_on_create_waits_for_tapir_1_7_3():
    # Tapir #906: the Create* commands take an id from 1.7.3; 1.7.2 refuses the field.
    item = dict(WALL, id="W-01")
    old = create_elements(Plan(tapir_version="1.7.2").connection(), "wall", [item, WALL])
    assert [s["index"] for s in old["skipped"]] == [0]
    assert "1.7.3" in old["skipped"][0]["reason"]
    assert "set_element_data" in old["skipped"][0]["reason"]
    new = create_elements(Plan(tapir_version="1.7.3").connection(), "wall", [item])
    assert new["ready"] == 1 and new["payload"]["wallsData"][0]["id"] == "W-01"
