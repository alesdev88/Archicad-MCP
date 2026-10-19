"""create_elements: every element Tapir can create, checked before it is sent.

The fake below answers like Tapir 1.5.x: Create* commands return one entry per
item, either {"elementId": {"guid"}} or {"error": {"code", "message"}}.
"""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create import CREATE_TYPES, create_elements
from archicad_mcp.gateway.registry import DEFINITIONS_DIR, _load_js_json
from tests.conftest import FakeCore
from tests.fixtures import api_replays

WALL = {"begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 5, "y": 0},
        "height": 3.0, "thickness": 0.2}


# GUIDs must be UUID-shaped: the item schemas reject anything else.
W1, W2 = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000a2"
S1, GONE = "00000000-0000-4000-8000-0000000000b1", "00000000-0000-4000-8000-0000000000ff"


def door(host: str, **extra) -> dict:
    return {"ownerWallId": {"guid": host}, "centerOffset": 1.0, **extra}


class Plan:
    """Element types by GUID, which elements Archicad would refuse, the favourites."""

    def __init__(self, types=None, refused=(), favorites=None, window="FloorPlan",
                 missing_commands=()):
        self.types = dict(types or {})
        self.refused = set(refused)
        self.favorites = dict(favorites or {})
        self.window = window
        self.missing_commands = set(missing_commands)
        self.created: list[tuple[str, dict]] = []
        self.create_errors: dict[int, dict] = {}

    def connection(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing_commands}
        tapir = dict(api_replays.TAPIR)
        tapir["GetCurrentWindowType"] = lambda p: {"currentWindowType": self.window}
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
    bad = {k: v for k, v in WALL.items() if k != "height"}
    result = create_elements(plan.connection(), "wall", [WALL, bad], dry_run=False)
    assert result["problems"][0]["index"] == 1
    assert "height" in result["problems"][0]["message"]
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
    assert "local Tapir fork" in result["error"]
    assert "Update Tapir" not in result["error"]
