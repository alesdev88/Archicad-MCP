"""modify_elements commits: Tapir-ordered sends, keep_elevation, readback.

The fake applies writes the way the Tapir source does (1.5.9 to 1.7.1):
SetDetailsOfElements answers doors and windows first, a story change keeps the
height above the story, a draw order step can do nothing and still answer
success.
"""
from archicad_mcp.core.modify import Planned, modify_elements
from archicad_mcp.core.modify_commit import setdetails_order
from tests.fixtures.modify_project import ModifyProject

SLAB = "05C7A02F-4251-6F40-8407-67893BEFF6AC"
OBJECT = "5349C8FC-A4BF-3548-A614-77BEB2CE4261"
MESH = "D4DB880C-8083-F749-B27F-0886B2D6044B"
FINISH_FLOOR = 51
WRITES = ("Modify", "SetDetails", "Move", "SetSuspend")


def _commit(project, changes):
    return modify_elements(project.connect(), changes, dry_run=False)


def test_commit_sends_setdetails_then_modify_and_reads_back():
    project = ModifyProject()
    out = _commit(project, [{"guid": SLAB, "layer": "Finish - Floor", "thickness": 0.25}])
    assert out == {"dry_run": False, "requested": 1, "modified": 1}
    names = project.command_names()
    assert names.index("SetDetailsOfElements") < names.index("ModifySlabs")
    assert project.elements[SLAB]["layerIndex"] == FINISH_FLOOR
    assert project.elements[SLAB]["details"]["thickness"] == 0.25


def test_setdetails_order_matches_tapir():
    def planned(guid, kind, **kw):
        return Planned(guid=guid, type=kind, id=None, entry={}, **kw)
    batch = [planned("S", "Slab", layer=51), planned("DOOR-1", "Door", layer=51),
             planned("WINDOW-1", "Window", draw_order=5),
             planned("DOOR-2", "Door", draw_order=9)]
    assert [p.guid for p in setdetails_order(batch)] == ["DOOR-1", "DOOR-2", "S", "WINDOW-1"]


def test_results_are_matched_by_tapir_order():
    project = ModifyProject()
    project.fail_setdetails = {SLAB: {"code": 6001, "message": "denied"}}
    out = _commit(project, [{"guid": SLAB, "layer": "Finish - Floor"},
                            {"guid": "DOOR-1", "layer": "Finish - Floor"},
                            {"guid": "WINDOW-1", "draw_order": 5},
                            {"guid": "DOOR-2", "draw_order": 9}])
    assert out["failed"] == [{"code": 6001, "message": "denied", "count": 1,
                              "sample": [{"guid": SLAB, "command": "SetDetailsOfElements"}]}]
    assert out["modified"] == 3


def test_failed_story_change_drops_the_modify_item():
    project = ModifyProject()
    project.fail_setdetails = {"BEAM-1": {"code": 1, "message": "no"}}
    out = _commit(project, [{"guid": "BEAM-1", "story": 1, "level": 0.5}])
    [group] = out["not_sent"]
    assert group["guids"] == ["BEAM-1"] and "story" in group["reason"]
    assert "ModifyBeams" not in project.command_names()
    assert out["modified"] == 0


def test_success_that_did_not_land_is_not_applied():
    project = ModifyProject()
    project.ignore_draw = {SLAB}
    out = _commit(project, [{"guid": SLAB, "draw_order": 1}])
    assert out["not_applied"] == [{"field": "draw_order", "count": 1,
                                   "sample": [{"guid": SLAB, "sent": 1, "read": 7}]}]
    assert out["modified"] == 0


def test_partly_applied_failures():
    project = ModifyProject()
    project.partial = {"BEAM-1"}
    out = _commit(project, [{"guid": "BEAM-1", "width": 0.3, "level": 0.5}])
    assert out["partly_applied"] == {"count": 1, "guids": ["BEAM-1"]}
    assert out["failed"][0]["sample"] == [{"guid": "BEAM-1", "command": "ModifyBeams"}]
    assert out["modified"] == 0


def test_keep_elevation_moves_back_with_groups_suspended():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1", "CW-1": "GROUP-1"}
    out = _commit(project, [{"guid": OBJECT, "story": 1, "keep_elevation": True}])
    writes = [n for n in project.command_names() if n.startswith(WRITES)]
    assert writes == ["SetDetailsOfElements", "SetSuspendGroupsMode", "MoveElements",
                      "SetSuspendGroupsMode"]
    [move] = project.sent("MoveElements")
    assert move["elementsWithMoveVectors"][0]["moveVector"] == {"x": 0, "y": 0, "z": -3.0}
    assert project.suspend_history == [True, False]
    assert project.elements[OBJECT]["floorIndex"] == 1
    assert project.elements[OBJECT]["details"]["origin"]["z"] == 0
    assert out["modified"] == 1


def test_suspend_groups_restored_when_move_raises():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1"}
    project.raise_on = {"MoveElements"}
    out = _commit(project, [{"guid": OBJECT, "story": 1, "keep_elevation": True}])
    assert "MoveElements" in out["stopped"]["message"]
    assert project.suspend_history[-1] is False and project.suspend is False


def test_raising_command_stops_and_reports():
    project = ModifyProject()
    project.raise_on = {"ModifySlabs"}
    out = _commit(project, [{"guid": SLAB, "layer": "Finish - Floor", "thickness": 0.25},
                            {"guid": MESH, "level": 0.3}])
    assert "ModifySlabs" in out["stopped"]["message"]
    [group] = out["not_sent"]
    assert group["guids"] == [MESH]
    assert [n["field"] for n in out["not_applied"]] == ["thickness"]
    assert project.elements[SLAB]["layerIndex"] == FINISH_FLOOR
    assert "ModifyMeshes" not in project.command_names()
    assert out["modified"] == 0


def test_group_mates_changed_are_reported():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1", "CW-1": "GROUP-1"}
    project.mate_changes = {"CW-1": {"layerIndex": 2}}
    out = _commit(project, [{"guid": OBJECT, "angle": 0.5}])
    assert out["group_mates_changed"] == {"count": 1, "guids": ["CW-1"]}
    assert out["modified"] == 1


def test_geometry_lists_are_unverified():
    project = ModifyProject()
    outline = [{"x": 0, "y": 0}, {"x": 3, "y": 0}, {"x": 3, "y": 3}, {"x": 0, "y": 3}]
    out = _commit(project, [{"guid": SLAB, "polygonOutline": outline, "holes": []}])
    assert out["unverified"] == {"holes": 1, "polygonOutline": 1}
    assert out["modified"] == 1


def test_commit_with_everything_skipped_sends_nothing():
    project = ModifyProject()
    out = _commit(project, [{"guid": "NOPE", "layer": "Finish - Floor"}])
    assert out["modified"] == 0 and out["skipped"][0]["guids"] == ["NOPE"]
    assert not [n for n in project.command_names() if n.startswith(WRITES)]
