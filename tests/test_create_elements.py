"""create_elements: every element Tapir can create, checked before it is sent.

The fake below answers like Tapir 1.5.x: Create* commands return one entry per
item, either {"elementId": {"guid"}} or {"error": {"code", "message"}}.
"""
import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create import CREATE_TYPES, create_elements
from archicad_mcp.gateway.registry import DEFINITIONS_DIR, _load_js_json, build_registry
from tests.conftest import FakeCore
from tests.fixtures import api_replays

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
    the loaded library parts."""

    def __init__(self, types=None, refused=(), favorites=None, window="FloorPlan",
                 missing_commands=(), elsewhere=(), library=None, pictures=(),
                 unread=(), archicad=29, tapir_version="1.8.2"):
        self.types = dict(types or {})
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
            {"type": self.types[e["elementId"]["guid"]], "floorIndex": 0, "layerIndex": 1}
            if e["elementId"]["guid"] in self.types
            else {"error": {"code": -2130313112, "message": "not found"}}
            for e in p["elements"]]}
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
