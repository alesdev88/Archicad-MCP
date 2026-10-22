"""modify_elements commits: Tapir-ordered sends, keep_elevation, readback.

The fake applies writes the way the Tapir source does (1.5.9 to 1.7.1):
SetDetailsOfElements answers doors and windows first, a story change keeps the
height above the story, a draw order step can do nothing and still answer
success.
"""
import math

import pytest

from archicad_mcp.core.modify import Planned, modify_elements
from archicad_mcp.core.modify_commit import setdetails_order
from tests.fixtures.learned_parts import LEAVES, bed_record, door_record, store
from tests.fixtures.modify_project import ModifyProject

SLAB = "05C7A02F-4251-6F40-8407-67893BEFF6AC"
OBJECT = "5349C8FC-A4BF-3548-A614-77BEB2CE4261"
MESH = "D4DB880C-8083-F749-B27F-0886B2D6044B"
SLAB_B = "SLAB-B"          # Basic, so thickness applies
FINISH_FLOOR = 51
WRITES = ("Modify", "SetDetails", "Move", "SetSuspend")


def _commit(project, changes):
    return modify_elements(project.connect(), changes, dry_run=False)


def test_commit_sends_setdetails_then_modify_and_reads_back():
    project = ModifyProject()
    out = _commit(project, [{"guid": SLAB_B, "layer": "Finish - Floor", "thickness": 0.25}])
    assert out == {"dry_run": False, "requested": 1, "modified": 1}
    names = project.command_names()
    assert names.index("SetDetailsOfElements") < names.index("ModifySlabs")
    assert project.elements[SLAB_B]["layerIndex"] == FINISH_FLOOR
    assert project.elements[SLAB_B]["details"]["thickness"] == 0.25


def test_commit_sends_nothing_to_an_element_of_another_database():
    project = ModifyProject()
    project.elsewhere = {"TEXT-1"}
    out = _commit(project, [{"guid": "TEXT-1", "layer": "Finish - Floor"}])
    assert out["modified"] == 0
    assert not [n for n in project.command_names() if n.startswith(WRITES)]


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


def _not_sent(out) -> dict[str, str]:
    return {g: group["reason"] for group in out.get("not_sent", []) for g in group["guids"]}


def test_raising_command_stops_and_reports():
    project = ModifyProject()
    project.raise_on = {"ModifySlabs"}
    out = _commit(project, [{"guid": SLAB_B, "layer": "Finish - Floor", "thickness": 0.25},
                            {"guid": MESH, "level": 0.3}])
    assert "ModifySlabs" in out["stopped"]["message"]
    reasons = _not_sent(out)
    assert "stopped before Archicad answered" in reasons[SLAB_B]
    assert "the run stopped" in reasons[MESH]
    assert "not_applied" not in out          # nothing answered success
    assert out["partly_applied"] == {"count": 1, "guids": [SLAB_B]}   # the layer landed
    assert project.elements[SLAB_B]["layerIndex"] == FINISH_FLOOR
    assert "ModifyMeshes" not in project.command_names()
    assert out["modified"] == 0


def test_a_stop_before_a_later_command_reports_what_landed():
    project = ModifyProject()
    project.raise_on = {"ModifyWalls"}       # walls go before slabs
    out = _commit(project, [{"guid": "WALL-1", "height": 2.8},
                            {"guid": SLAB_B, "layer": "Finish - Floor", "thickness": 0.25}])
    reasons = _not_sent(out)
    assert "stopped before Archicad answered" in reasons["WALL-1"]
    assert "the run stopped" in reasons[SLAB_B]
    assert out["partly_applied"]["guids"] == [SLAB_B]
    assert "not_applied" not in out


def test_a_readback_that_fails_still_reports():
    # The connection dies at the move: the move, the restore and the readback
    # all raise. The story change has already lifted the slab.
    project = ModifyProject()
    project.dead_after = "MoveElements"
    out = _commit(project, [{"guid": SLAB, "story": 1, "keep_elevation": True}])
    assert out["modified"] == 0
    assert "ongoing user input" in out["stopped"]["message"]
    assert out["unconfirmed"]["guids"] == [SLAB]
    assert "dry run" in out["unconfirmed"]["note"]


def test_suspend_groups_that_will_not_turn_on_skips_the_move():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1"}
    project.suspend_fail = {True}
    out = _commit(project, [{"guid": OBJECT, "story": 1, "keep_elevation": True}])
    assert "MoveElements" not in project.command_names()
    assert "Suspend Groups" in _not_sent(out)[OBJECT]
    assert out["partly_applied"]["guids"] == [OBJECT]     # it moved with the story
    assert out["modified"] == 0


def test_suspend_groups_left_on_is_reported():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1"}
    project.suspend_fail = {False}
    out = _commit(project, [{"guid": OBJECT, "story": 1, "keep_elevation": True}])
    assert "left on" in out["suspend_groups"]
    assert project.suspend is True


def test_a_failed_move_is_named_in_the_skip():
    project = ModifyProject()
    project.fail = {("MoveElements", OBJECT): {"code": 5, "message": "cannot move"}}
    out = _commit(project, [{"guid": OBJECT, "story": 1, "keep_elevation": True,
                             "angle": 0.5}])
    reason = _not_sent(out)[OBJECT]
    assert "move" in reason and "story, layer or draw order" not in reason
    assert "ModifyObjects" not in project.command_names()


def test_a_draw_order_reset_is_unverified():
    project = ModifyProject()
    out = _commit(project, [{"guid": SLAB, "draw_order": 0}])
    assert out["unverified"] == {"draw_order": 1}
    assert out["modified"] == 1


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


# ---------- placement: the fields it works out, and the leaves written after ----------

BED_PART = "B5D0A1C2-7E4F-4C11-9A3B-0D6E2F8A1B01"
DOOR_PART = "D0E2C4A6-1B3D-4F5A-8C7E-9A0B1C2D3E01"
# Hinged at WALL-1's begin jamb (the door's centre is 1 m along it), opening left.
TO_BEGIN_LEFT = {"hinge_near": {"x": 0.6, "y": 0.0}, "opens_toward": {"x": 1.0, "y": 2.0}}


def _door(tmp_path):
    project = ModifyProject()
    project.set_part("DOOR-1", "Door", DOOR_PART)
    project.elements["DOOR-1"]["details"]["centerOffset"] = 1.0
    return project, store(tmp_path, door_record(guid=DOOR_PART, leaves=2, leaf_states=LEAVES))


def _with_leaves(state="left open") -> list[dict]:
    return [{"guid": "DOOR-1", "placement": {**TO_BEGIN_LEFT, "leaves": state}}]


def test_a_placed_bed_lands_turned_on_its_story_and_height(tmp_path):
    project = ModifyProject()
    project.set_part(OBJECT, "Bed Double 01", BED_PART)
    project.elements[OBJECT]["floorIndex"] = 1
    project.elements[OBJECT]["details"]["origin"]["z"] = 3.4
    placement = {"center": {"x": 2.0, "y": 3.0}, "head": {"x": 0.0, "y": -1.0},
                 "size": {"length": 2.0, "width": 1.6, "height": 0.5}}
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": placement}],
                          dry_run=False, records=store(tmp_path, bed_record(guid=BED_PART)))
    assert out == {"dry_run": False, "requested": 1, "modified": 1}
    assert "SetDetailsOfElements" not in project.command_names()
    bed = project.elements[OBJECT]
    assert bed["floorIndex"] == 1
    assert bed["details"]["origin"] == pytest.approx({"x": 2.0, "y": 4.0, "z": 3.4})
    assert bed["details"]["angle"] == pytest.approx(math.pi)


def test_leaves_are_written_after_the_commit(tmp_path):
    project, records = _door(tmp_path)
    out = modify_elements(project.connect(), _with_leaves(), dry_run=False, records=records)
    assert project.parameter_writes == [{"elementsWithGDLParameters": [
        {"elementId": {"guid": "DOOR-1"},
         "gdlParameters": [{"name": "gs_leaf_1_open", "value": 1},
                           {"name": "gs_leaf_2_open", "value": 0}]}]}]
    names = project.command_names()
    readback = len(names) - 1 - names[::-1].index("GetDetailsOfElements")
    assert names.index("ModifyDoors") < readback < names.index("SetGDLParametersOfElements")
    assert out["modified"] == 1
    assert out["unverified"] == {"leaves": 1}      # GDL parameters are not read back
    assert "placed_not_set" not in out
    door = project.elements["DOOR-1"]["details"]
    assert (door["reflected"], door["oSide"]) == (True, True)


def _refuse(project):
    project.parameter_error = {"code": -2130313215, "message": "Failed to change the parameters"}


def _time_out(project):
    project.raise_on = {"SetGDLParametersOfElements"}


def _crash(project):
    def boom(params):
        raise RuntimeError("the fake broke")
    project.core.tapir_responses["SetGDLParametersOfElements"] = boom


@pytest.mark.parametrize("make_it_fail, message", [
    (_refuse, "Failed to change the parameters"),
    (_time_out, "SetGDLParametersOfElements timed out"),
    (_crash, "the write stopped on RuntimeError: the fake broke"),
])
def test_a_refused_leaves_write_is_reported_under_placed_not_set(tmp_path, make_it_fail,
                                                                 message):
    project, records = _door(tmp_path)
    conn = project.connect()
    make_it_fail(project)
    out = modify_elements(conn, _with_leaves(), dry_run=False, records=records)
    [unset] = out["placed_not_set"]
    assert unset == {"guid": "DOOR-1", "leaves": "left open",
                     "parameters": LEAVES["left open"], "message": unset["message"]}
    assert message in unset["message"]
    assert out["modified"] == 1                    # the hinge and swing landed
    assert "unverified" not in out
    # Nothing else is sent to the door after the refusal.
    assert project.command_names()[-1] == "SetGDLParametersOfElements"


def test_leaves_are_not_written_when_the_doors_change_fails(tmp_path):
    project, records = _door(tmp_path)
    project.fail = {("ModifyDoors", "DOOR-1"): {"code": 9, "message": "refused"}}
    out = modify_elements(project.connect(), _with_leaves(), dry_run=False, records=records)
    assert "SetGDLParametersOfElements" not in project.command_names()
    [unset] = out["placed_not_set"]
    assert unset["guid"] == "DOOR-1" and unset["message"].startswith("not written")
    assert out["failed"][0]["sample"] == [{"guid": "DOOR-1", "command": "ModifyDoors"}]


def test_leaves_are_not_written_after_the_run_stopped(tmp_path):
    project, records = _door(tmp_path)
    project.raise_on = {"ModifyDoors"}
    out = modify_elements(project.connect(), _with_leaves(), dry_run=False, records=records)
    assert "SetGDLParametersOfElements" not in project.command_names()
    [unset] = out["placed_not_set"]
    assert "the run stopped" in unset["message"]
