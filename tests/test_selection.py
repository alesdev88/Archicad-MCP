"""set_selection against a selection that really changes (tests/conftest.track_selection).

Tapir runs every add before every remove, so the replace must not remove what
it adds; and Archicad answers success per element while leaving some of them
unselected, so the count comes from reading the selection back.
"""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.selection import set_selection
from tests.conftest import FakeCore, track_selection
from tests.fixtures import api_replays

WALL = "A1B2C3D4-0000-4000-8000-000000000001"
SLAB = "A1B2C3D4-0000-4000-8000-000000000002"
DOOR = "A1B2C3D4-0000-4000-8000-000000000003"
SECTION_OBJECT = "A1B2C3D4-0000-4000-8000-000000000004"


def _conn(core: FakeCore) -> ArchicadConnection:
    return ArchicadConnection(19723, core=core)


def _core() -> FakeCore:
    return FakeCore(official=dict(api_replays.OFFICIAL), tapir=dict(api_replays.TAPIR))


def test_an_already_selected_element_stays_selected():
    core = _core()
    state = track_selection(core, selected=(WALL, SLAB))
    result = set_selection(_conn(core), [WALL, DOOR])
    assert state == {WALL, DOOR}
    assert result["selected"] == 2


def test_an_already_selected_element_is_matched_without_case():
    core = _core()
    state = track_selection(core, selected=(WALL,))
    set_selection(_conn(core), [WALL.lower()])
    assert state == {WALL}


def test_selected_counts_what_archicad_really_selected():
    core = _core()
    track_selection(core, selected=(SLAB,), unselectable=(SECTION_OBJECT,))
    result = set_selection(_conn(core), [WALL, SECTION_OBJECT])
    assert result["requested"] == 2
    assert result["selected"] == 1
    assert result["not_selected"] == [SECTION_OBJECT]


def test_a_full_selection_reports_no_misses():
    core = _core()
    track_selection(core, selected=(SLAB,))
    result = set_selection(_conn(core), [WALL])
    assert result == {"requested": 1, "selected": 1, "coverage": "whole-plan"}


def test_a_partial_read_back_says_so():
    """Without Tapir's GetSelectedElements the read misses markers and MEP
    routes, so a miss may not be one: the result carries the coverage note."""
    core = _core()
    track_selection(core)
    core.official_responses["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] != "GetSelectedElements"}
    core.official_responses["API.GetSelectedElements"] = {"elements": []}
    result = set_selection(_conn(core), [WALL])
    assert result["coverage"] == "model-elements-only"
    assert "coverage_note" in result
