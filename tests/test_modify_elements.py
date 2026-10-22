"""modify_elements planning and dry run, against a fake Tapir built from the
answers recorded on MCP-Test (tests/fixtures/modify_elements)."""
import copy
import math

import pytest

from archicad_mcp.core.modify import modify_elements, plan_changes
from archicad_mcp.core.part_records import PartRecords
from tests.fixtures.learned_parts import LEAVES, bed_record, door_record, store
from tests.fixtures.modify_project import ModifyProject

SLAB = "05C7A02F-4251-6F40-8407-67893BEFF6AC"     # SLA - 001, story 0, layer 33
SLAB2 = "8BD33B9C-5B7D-5C46-8B28-9EC1CB9310CC"    # SLA - 002, story 1, z 3.25
OBJECT = "5349C8FC-A4BF-3548-A614-77BEB2CE4261"   # FU - 003, story 0, layer 3
MESH = "D4DB880C-8083-F749-B27F-0886B2D6044B"     # TR - 005, story 1, layer 45
SLAB_B = "SLAB-B"                                 # SLA - 101, Basic, story 0, layer 33

WRITES = ("Modify", "SetDetails", "Move", "SetSuspend", "Lock", "Unlock", "SetGDL")


def _dry(project, changes):
    return modify_elements(project.connect(), changes)


def _reason(out, guid):
    for group in out.get("skipped", []):
        if guid in group["guids"]:
            return group["reason"]
    raise AssertionError(f"{guid} not skipped: {out}")


def _warnings(out, guid):
    return [w["warning"] for w in out.get("warnings", []) if guid in w["guids"]]


def _planned(out, guid):
    [entry] = [p for p in out["planned"] if p["guid"] == guid]
    return entry


def test_dry_run_shows_current_and_new_per_field():
    out = _dry(ModifyProject(), [{"guid": SLAB_B, "layer": "Finish - Floor", "thickness": 0.25}])
    assert out["dry_run"] is True and out["requested"] == 1 and out["planned_count"] == 1
    entry = _planned(out, SLAB_B)
    assert entry["id"] == "SLA - 101" and entry["type"] == "Slab"
    assert entry["changes"] == {
        "layer": {"current": "Structural - Bearing", "new": "Finish - Floor"},
        "thickness": {"current": 0.2, "new": 0.25}}
    assert "skipped" not in out


def test_dry_run_sends_nothing():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1"}
    _dry(project, [{"guid": SLAB, "layer": "Finish - Floor", "story": 1, "keep_elevation": True},
                   {"guid": OBJECT, "angle": 0.5, "draw_order": 1},
                   {"guid": MESH, "level": 0.3}])
    assert not [name for name, _ in project.core.calls if name.startswith(WRITES)]


def test_layer_names_match_exactly_then_trimmed_and_case_insensitive():
    out = _dry(ModifyProject(), [{"guid": SLAB, "layer": "interior - furniture"},
                                 {"guid": OBJECT, "layer": "Nope"}])
    assert _planned(out, SLAB)["changes"]["layer"]["new"] == "Interior - Furniture "
    assert "no layer named 'Nope'" in _reason(out, OBJECT)


def test_story_by_index_or_name():
    out = _dry(ModifyProject(), [{"guid": SLAB2, "story": "Ground Floor"},
                                 {"guid": SLAB, "story": 7},
                                 {"guid": MESH, "story": ""}])
    assert _planned(out, SLAB2)["changes"]["story"] == {"current": 1, "new": 0}
    assert "no story 7" in _reason(out, SLAB)
    assert "no story named ''" in _reason(out, MESH)


def test_story_change_warns_how_far_it_moves():
    moves = _dry(ModifyProject(), [{"guid": SLAB, "story": 1}])
    assert any("moves by +3.00 m" in w for w in _warnings(moves, SLAB))
    stays = _dry(ModifyProject(), [{"guid": SLAB, "story": 1, "keep_elevation": True}])
    assert any("stays at the same height" in w and "-3.00 m" in w
               for w in _warnings(stays, SLAB))


def test_keep_elevation_rules():
    out = _dry(ModifyProject(), [{"guid": SLAB, "keep_elevation": True, "layer": "Finish - Floor"},
                                 {"guid": "TEXT-1", "story": 1, "keep_elevation": True},
                                 {"guid": MESH, "story": 0, "keep_elevation": "yes"}])
    assert "give 'story' too" in _reason(out, SLAB)
    assert "no elevation" in _reason(out, "TEXT-1")
    assert "true or false" in _reason(out, MESH)


def test_unknown_field_lists_the_allowed_ones():
    out = _dry(ModifyProject(), [{"guid": SLAB, "height": 3}])
    reason = _reason(out, SLAB)
    assert "ModifySlabs has no field 'height'" in reason and "thickness" in reason


def test_layer_only_change_on_a_type_without_a_command():
    out = _dry(ModifyProject(), [{"guid": "CW-1", "layer": "Shell - General"},
                                 {"guid": "HATCH-1", "thickness": 0.2}])
    assert _planned(out, "CW-1")["changes"]["layer"]["new"] == "Shell - General"
    assert "only story, layer and draw_order" in _reason(out, "HATCH-1")


def test_refusals_from_the_field_table():
    out = _dry(ModifyProject(), [
        {"guid": OBJECT, "offset": {"x": 1, "y": 0}},
        {"guid": "MORPH-1", "rotationDegreesZ": 90},
        {"guid": "DOOR-1", "story": 1},
        {"guid": SLAB, "story": 1, "zCoordinate": 3.0},
        {"guid": "ROOF-S", "thickness": 0.2},
    ])
    assert "discards" in _reason(out, OBJECT)
    assert "xAxis" in _reason(out, "MORPH-1")
    assert "host" in _reason(out, "DOOR-1")
    assert "fight" in _reason(out, SLAB)
    assert "single-plane" in _reason(out, "ROOF-S")
    assert out["planned_count"] == 0


def test_attribute_names_are_resolved_and_shown_by_name():
    project = ModifyProject()
    changes = [{"guid": SLAB, "compositeId": "Slab 250"},
               {"guid": SLAB2, "compositeId": "Nope"},
               {"guid": "WALL-1", "sideMaterial": "Paint - White",
                "cutFillPen": {"penIndex": 5}},
               {"guid": MESH, "lineTypeIndex": "Dashed"}]
    out = _dry(project, changes)
    assert _planned(out, SLAB)["changes"]["compositeId"] == {"current": "Generic",
                                                            "new": "Slab 250"}
    assert "no Composite named 'Nope'" in _reason(out, SLAB2)
    plan = plan_changes(project.connect(), changes)
    items = {p.guid: p.item for p in plan.planned}
    assert items[SLAB]["compositeId"] == {"guid": project.attribute_guid("Composite", "Slab 250")}
    assert items["WALL-1"]["sideMaterial"] == {
        "overridden": True,
        "attributeId": {"guid": project.attribute_guid("Surface", "Paint - White")}}
    assert items["WALL-1"]["cutFillPen"] == {"overridden": True, "penIndex": 5}
    assert items[MESH]["lineTypeIndex"] == 4


def test_partial_coordinates_are_completed_and_rehoming_warned():
    project = ModifyProject()
    project.elements[OBJECT]["floorIndex"] = 1
    project.elements[OBJECT]["details"]["origin"]["z"] = 2.5
    changes = [{"guid": OBJECT, "coordinates": {"x": 5}}]
    out = _dry(project, changes)
    [planned] = plan_changes(project.connect(), changes).planned
    assert planned.item["coordinates"] == {"x": 5, "y": 0, "z": 2.5}
    assert _planned(out, OBJECT)["changes"]["coordinates"] == {
        "current": {"x": 3.676672178, "y": 0, "z": 2.5}, "new": {"x": 5, "y": 0, "z": 2.5}}
    assert any("moves from story 1 to story 0" in w for w in _warnings(out, OBJECT))


def test_schema_failures_are_skips():
    out = _dry(ModifyProject(), [{"guid": SLAB_B, "thickness": 0},
                                 {"guid": OBJECT, "draw_order": 15},
                                 {"guid": MESH, "draw_order": 0}])
    assert "thickness" in _reason(out, SLAB_B) and "minimum" in _reason(out, SLAB_B)
    assert "0 to 14" in _reason(out, OBJECT)
    assert _planned(out, MESH)["changes"]["draw_order"] == {"current": 7, "new": 0}


def test_editability_and_lock_reasons():
    project = ModifyProject()
    project.hidden = {MESH}
    project.locked_property = {OBJECT}               # IsEditable still says editable
    project.elements[SLAB2]["hotlinkId"] = {"guid": "HOTLINK-1"}
    project.not_editable = {SLAB2, "WALL-1"}
    project.elements["WALL-1"]["layerIndex"] = 56
    project.locked_layers = {56}
    out = _dry(project, [{"guid": g, "layer": "Finish - Floor"}
                         for g in (MESH, OBJECT, SLAB2, "WALL-1", SLAB)])
    assert "hidden layer" in _reason(out, MESH)
    assert "Edit > Locking" in _reason(out, OBJECT)
    assert "hotlinked module" in _reason(out, SLAB2)
    assert "'- Hidden'" in _reason(out, "WALL-1")
    assert out["planned_count"] == 1


def test_layout_window_refuses_with_the_window_named():
    project = ModifyProject(window="Layout")
    out = _dry(project, [{"guid": SLAB, "layer": "Finish - Floor"},
                         {"guid": OBJECT, "angle": 1.0}])
    assert out["planned_count"] == 0
    assert all("Layout" in g["reason"] for g in out["skipped"])
    assert out["active_window"] == "Layout"


def test_grouped_elements_warn_with_the_suspend_state():
    project = ModifyProject()
    project.groups = {OBJECT: "GROUP-1", "CW-1": "GROUP-1"}
    out = _dry(project, [{"guid": OBJECT, "angle": 0.5}, {"guid": SLAB_B, "thickness": 0.25}])
    assert any("Suspend Groups is off" in w for w in _warnings(out, OBJECT))
    assert _planned(out, SLAB_B) and not _warnings(out, SLAB_B)


def test_old_or_missing_tapir_is_refused_before_any_read():
    old = ModifyProject(version="1.5.8")
    out = _dry(old, [{"guid": SLAB, "thickness": 0.2}])
    assert "1.5.9" in out["error"] and "1.5.8" in out["error"]
    assert "GetDetailsOfElements" not in [name for name, _ in old.core.calls]
    missing = ModifyProject(tapir=False)
    assert "Tapir" in _dry(missing, [{"guid": SLAB, "thickness": 0.2}])["error"]


def test_object_dimensions_depend_on_the_tapir_version():
    old = ModifyProject(version="1.5.10")
    assert "1.6.0" in _reason(_dry(old, [{"guid": OBJECT, "dimensions": {"x": 1.2}}]), OBJECT)
    new = ModifyProject(version="1.7.1")
    [planned] = plan_changes(new.connect(), [{"guid": OBJECT, "dimensions": {"x": 1.2}}]).planned
    assert planned.item["dimensions"] == {"x": 1.2, "y": 1, "z": 1.89}


def test_duplicate_guids_merge_and_conflicts_skip():
    out = _dry(ModifyProject(), [{"guid": SLAB_B, "layer": "Finish - Floor"},
                                 {"guid": SLAB_B, "thickness": 0.25},
                                 {"guid": OBJECT, "angle": 0.5},
                                 {"guid": OBJECT, "angle": 0.7}])
    assert out["requested"] == 2
    assert set(_planned(out, SLAB_B)["changes"]) == {"layer", "thickness"}
    assert "two different values" in _reason(out, OBJECT)


def test_planned_list_is_capped():
    project = ModifyProject()
    template = project.elements[SLAB]
    guids = [f"S-{i:02d}" for i in range(60)]
    for g in guids:
        project.elements[g] = copy.deepcopy(template)
    out = _dry(project, [{"guid": g, "layer": "Finish - Floor"} for g in guids])
    assert out["planned_count"] == 60 and len(out["planned"]) == 50 and out["not_shown"] == 10


def test_missing_command_is_a_skip():
    project = ModifyProject()
    project.missing_commands = {"ModifyMeshes"}
    reason = _reason(_dry(project, [{"guid": MESH, "level": 0.3}]), MESH)
    assert "ModifyMeshes" in reason and "1.5.4" in reason


def test_malformed_change_refuses_the_call():
    project = ModifyProject()
    assert _dry(project, [{"guid": SLAB, "layer": "X"}, {"layer": "X"}]) == \
        {"error": "change 2 has no guid"}
    assert project.core.calls == []


def test_long_values_are_summarized():
    project = ModifyProject()
    polygon = project.elements[MESH]["details"]["polygonCoordinates"]
    out = _dry(project, [{"guid": MESH, "polygonCoordinates": polygon}])
    assert _planned(out, MESH)["changes"]["polygonCoordinates"] == {
        "current": "25 points", "new": "25 points"}
    assert any("removes the holes" in w for w in _warnings(out, MESH))


def test_lowercase_guids_plan_like_uppercase():
    out = _dry(ModifyProject(), [{"guid": SLAB.lower(), "referencePlaneLocation": "Top"}])
    assert out["planned"][0]["guid"] == SLAB


def test_empty_and_fieldless_changes():
    project = ModifyProject()
    assert _dry(project, []) == {"dry_run": True, "requested": 0, "planned_count": 0}
    assert project.core.calls == []
    out = _dry(ModifyProject(), [{"guid": SLAB, "keep_elevation": False}])
    assert "nothing to change" in _reason(out, SLAB)


def test_door_draw_order_warns_about_the_wall_and_its_openings():
    out = _dry(ModifyProject(), [{"guid": "DOOR-1", "draw_order": 0}])
    assert any("every opening" in w for w in _warnings(out, "DOOR-1"))


def test_keep_elevation_with_a_relative_level_is_refused():
    out = _dry(ModifyProject(), [{"guid": "BEAM-1", "story": 1, "keep_elevation": True,
                                  "level": 0.5}])
    assert "keep_elevation" in _reason(out, "BEAM-1")


def test_large_objects_show_only_the_keys_that_change():
    out = _dry(ModifyProject(), [{"guid": SLAB, "floorFill": {"orientation": {"matrix00": 2}}},
                                 {"guid": "TEXT-1", "style": {"height": 3.5}}])
    assert _planned(out, SLAB)["changes"]["floorFill"] == {
        "current": {"orientation": {"matrix00": 1}}, "new": {"orientation": {"matrix00": 2}}}
    assert _planned(out, "TEXT-1")["changes"]["style"] == {
        "current": {"height": 2.5}, "new": {"height": 3.5}}


def test_an_element_the_details_read_cannot_find_is_skipped():
    # An element IsEditable passes but GetDetailsOfElements does not find (as
    # floor-plan labels from a Layout, 28.09.2026) is skipped as not found. From
    # the floor plan the details read finds other views' elements; the next
    # test covers those.
    project = ModifyProject()
    project.other_database = {"TEXT-1"}
    out = _dry(project, [{"guid": "TEXT-1", "layer": "Finish - Floor"}])
    assert "not found" in _reason(out, "TEXT-1")


def test_an_element_of_another_database_seen_from_the_floor_plan_is_skipped():
    # Write test on MCP-Test, 19.10.2026, floor plan active: IsEditable passed
    # a layout Text, the details read found it, and MoveElements answered
    # success and left it. Tapir GetAllElements did not list it.
    project = ModifyProject()
    project.elsewhere = {"TEXT-1"}
    out = _dry(project, [{"guid": "TEXT-1", "layer": "Finish - Floor"},
                         {"guid": SLAB_B, "layer": "Finish - Floor"}])
    assert _reason(out, "TEXT-1").startswith("in another view's database")
    assert [p["guid"] for p in out["planned"]] == [SLAB_B]
    for read in project.sent("API.GetPropertyValuesOfElements"):
        assert {"elementId": {"guid": "TEXT-1"}} not in read["elements"]


def test_thickness_on_a_composite_slab_is_refused():
    # Live 19.10.2026: Archicad kept the composite's thickness and answered success.
    out = _dry(ModifyProject(), [{"guid": SLAB, "thickness": 0.25},
                                 {"guid": SLAB2, "thickness": 0.25, "structureType": "Basic",
                                  "buildingMaterialId": "Concrete"}])
    assert "Basic" in _reason(out, SLAB) and "Composite" in _reason(out, SLAB)
    assert _planned(out, SLAB2)["changes"]["thickness"] == {"current": 0.3, "new": 0.25}


# ---------- placement: a placed learned part corrected by what the sketch shows ----------

BED_PART = "B5D0A1C2-7E4F-4C11-9A3B-0D6E2F8A1B01"     # main GUID of "Bed Double 01"
DOOR_PART = "D0E2C4A6-1B3D-4F5A-8C7E-9A0B1C2D3E01"    # main GUID of "Door"
# WALL-1 runs from (0, 0) to (5, 0): left of it is +y.


def _bed_project(tmp_path, **record):
    """OBJECT as a placed Bed Double 01 (at (3.68, 0), story 0, angle 0, 1 x 1 x 1.89)."""
    project = ModifyProject()
    project.set_part(OBJECT, "Bed Double 01", BED_PART)
    return project, store(tmp_path, bed_record(guid=BED_PART, **record))


def _bed(head=None, **placement) -> dict:
    """2 m long and 1.6 m wide, centred on (2, 3), the head pointing -y."""
    shown = {"center": {"x": 2.0, "y": 3.0},
             "size": {"length": 2.0, "width": 1.6, "height": 0.5},
             "head": head or {"x": 0.0, "y": -1.0}}
    shown.update(placement)
    return shown


def _door_project(tmp_path, center_offset=1.0, **record):
    """DOOR-1 as a placed "Door" in WALL-1, its centre 1 m along the wall, hinged at
    the end jamb and opening left (reflected false, oSide true: ft), with refSide false
    as a door placed through the API reads it (Probe results (d))."""
    project = ModifyProject()
    project.set_part("DOOR-1", "Door", DOOR_PART)
    project.elements["DOOR-1"]["details"]["centerOffset"] = center_offset
    project.elements["DOOR-1"]["details"]["refSide"] = False
    return project, store(tmp_path, door_record(guid=DOOR_PART, **record))


def _swing(hinge_x: float, toward_y: float, **placement) -> dict:
    return {"hinge_near": {"x": hinge_x, "y": 0.0}, "opens_toward": {"x": 1.0, "y": toward_y},
            **placement}


def _writes(project) -> list[str]:
    return [name for name in project.command_names() if name.startswith(WRITES)]


def test_a_placed_bed_turned_by_its_head_shows_new_coordinates_and_angle(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed()}],
                          records=records)
    entry = _planned(out, OBJECT)
    changes = entry["changes"]
    # The head (local +y) turns to plan -y: half a turn.
    assert changes["angle"]["current"] == 0
    assert changes["angle"]["new"] == pytest.approx(math.pi)
    # Half a turn about the foot end: the insertion point goes half the length north
    # of the centre, so the bed covers x 1.2 to 2.8, y 2 to 4, its head at y 2.
    assert changes["coordinates"]["current"] == {"x": 3.676672178, "y": 0, "z": 0}
    assert changes["coordinates"]["new"] == pytest.approx({"x": 2.0, "y": 4.0, "z": 0})
    assert changes["dimensions"] == {"current": {"x": 1, "y": 1, "z": 1.89},
                                     "new": {"x": 1.6, "y": 2.0, "z": 0.5}}
    assert changes["reflected"] == {"current": False, "new": False}
    assert entry["placement"] == {"part": "Bed Double 01"}
    assert "skipped" not in out and "warnings" not in out
    assert _writes(project) == []


def test_a_placed_object_keeps_its_story_and_elevation(tmp_path):
    project, records = _bed_project(tmp_path)
    project.elements[OBJECT]["floorIndex"] = 1
    project.elements[OBJECT]["details"]["origin"]["z"] = 3.4     # 0.4 m above story 1
    changes = [{"guid": OBJECT, "placement": _bed()}]
    [planned] = plan_changes(project.connect(), changes, records=records).planned
    assert planned.item["coordinates"]["z"] == 3.4
    assert planned.story is None
    out = modify_elements(project.connect(), changes, records=records)
    assert "story" not in _planned(out, OBJECT)["changes"]
    assert not _warnings(out, OBJECT)      # no "moves from story 1 to story ..."


def test_a_height_in_the_centre_is_refused(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(),
                          [{"guid": OBJECT, "placement": _bed(center={"x": 2, "y": 3, "z": 0.4})}],
                          records=records)
    [error] = out.values()
    assert error.startswith(f"change 1 ({OBJECT}): placement.center takes x and y only")
    assert "story" in error and "coordinates" in error
    assert _writes(project) == []


def test_the_height_falls_back_to_the_record_then_the_element(tmp_path):
    no_height = {"length": 2.0, "width": 1.6}
    project, records = _bed_project(tmp_path, height=0.45)
    [planned] = plan_changes(project.connect(), [{"guid": OBJECT,
                                                  "placement": _bed(size=no_height)}],
                             records=records).planned
    assert planned.item["dimensions"] == {"x": 1.6, "y": 2.0, "z": 0.45}

    project, records = _bed_project(tmp_path / "current")
    [planned] = plan_changes(project.connect(), [{"guid": OBJECT,
                                                  "placement": _bed(size=no_height)}],
                             records=records).planned
    assert planned.item["dimensions"] == {"x": 1.6, "y": 2.0, "z": 1.89}

    del project.elements[OBJECT]["details"]["dimensions"]
    out = modify_elements(project.connect(), [{"guid": OBJECT,
                                               "placement": _bed(size=no_height)}],
                          records=records)
    assert out == {"error": f"change 1 ({OBJECT}): give size.height in metres: Tapir 1.7.2 "
                            "cannot read a part's own height"}


def test_a_footprint_off_the_size_asked_warns_and_one_of_no_width_is_an_error(tmp_path):
    headboard = {"xMin": [-0.5, 0], "xMax": [0.5, 0], "yMin": [0, 0], "yMax": [1, 0.1]}
    project, records = _bed_project(tmp_path, footprint=headboard)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed()}],
                          records=records)
    [warning] = _warnings(out, OBJECT)
    assert warning.startswith("covers 2.10 m along its head and 1.60 m across")

    flat = {"xMin": [-1, 0.5], "xMax": [1, -0.5], "yMin": [-1, 0.5], "yMax": [1, -0.5]}
    project, records = _bed_project(tmp_path / "flat", footprint=flat)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed(
        size={"length": 0.5, "width": 0.5, "height": 0.9})}], records=records)
    assert out["error"].startswith(f"change 1 ({OBJECT}): the footprint recorded for "
                                   "'Bed Double 01' covers 0.00 x 0.00 m")


def test_a_door_hinged_at_the_other_jamb_flips_reflected(tmp_path):
    project, records = _door_project(tmp_path)
    # The hinge moves to the begin jamb, still opening left: tt.
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    entry = _planned(out, "DOOR-1")
    assert entry["changes"] == {"reflected": {"current": False, "new": True},
                                "oSide": {"current": True, "new": True}}
    assert entry["placement"] == {"part": "Door", "hinge": "begin", "swing": "left"}
    # The wall is the door's owner, read for its begin and end points.
    assert {"elements": [{"elementId": {"guid": "WALL-1"}}]} in project.sent(
        "GetDetailsOfElements")
    assert _writes(project) == []


def test_a_door_with_ref_side_true_cannot_be_placed(tmp_path):
    project, records = _door_project(tmp_path)
    project.elements["DOOR-1"]["details"]["refSide"] = True
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    [error] = out.values()
    assert error.startswith("change 1 (DOOR-1): ") and "refSide true" in error
    assert "refSide false" in error and "reflected and oSide" in error
    # Nor can a change that turns it to refSide true.
    project, records = _door_project(tmp_path / "turned")
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "refSide": True,
                                               "placement": _swing(0.6, 2.0)}], records=records)
    assert "refSide true" in out["error"]
    assert _writes(project) == []


def test_a_door_in_a_wall_flipped_otherwise_than_where_it_was_learned_warns(tmp_path):
    # WALL-1 has flipped false.
    project, records = _door_project(tmp_path, wall_flipped=True)
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    [warning] = _warnings(out, "DOOR-1")
    assert warning.startswith("stands in a wall with flipped false, and 'Door' was learned "
                              "in a wall with flipped true")
    project, records = _door_project(tmp_path / "same", wall_flipped=False)
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    assert not _warnings(out, "DOOR-1")


def test_a_door_in_a_curved_wall_cannot_be_placed(tmp_path):
    project, records = _door_project(tmp_path)
    project.elements["WALL-1"]["details"]["arcAngle"] = 1.2
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    assert out["error"].startswith("change 1 (DOOR-1): wall WALL-1 is curved")


def test_a_hinge_point_at_the_doors_centre_makes_the_call_an_error(tmp_path):
    project, records = _door_project(tmp_path)
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(1.0, 2.0)}],
                          records=records)
    assert out["error"].startswith("change 1 (DOOR-1): placement.hinge_near")


def test_a_doors_centre_comes_from_its_details_unless_the_change_gives_one(tmp_path):
    project, records = _door_project(tmp_path, center_offset=3.0)
    # 2.5 m along the wall lies before the centre at 3.0: the begin jamb, opening left.
    [kept] = plan_changes(project.connect(), [{"guid": "DOOR-1", "placement": _swing(2.5, 2.0)}],
                          records=records).planned
    assert (kept.item["reflected"], kept.item["oSide"]) == (True, True)
    # Moved to 2.0, the same point lies past the centre: the end jamb.
    [moved] = plan_changes(project.connect(), [{"guid": "DOOR-1", "centerOffset": 2.0,
                                                "placement": _swing(2.5, 2.0)}],
                           records=records).planned
    assert moved.item == {"centerOffset": 2.0, "reflected": False, "oSide": True}


def test_a_centre_given_in_another_change_moves_the_hinge_too(tmp_path):
    project, records = _door_project(tmp_path, center_offset=1.0)
    # Moved to 3.0 by its own change, 2.5 m along the wall lies before the centre: the
    # begin jamb, opening left (tt), as when both are in one change.
    together = [{"guid": "DOOR-1", "centerOffset": 3.0, "placement": _swing(2.5, 2.0)}]
    apart = [{"guid": "DOOR-1", "centerOffset": 3.0},
             {"guid": "DOOR-1", "placement": _swing(2.5, 2.0)}]
    for changes in (together, apart):
        [planned] = plan_changes(project.connect(), changes, records=records).planned
        assert planned.item == {"centerOffset": 3.0, "reflected": True, "oSide": True}


def test_leaves_show_in_the_dry_run_and_nothing_is_written(tmp_path):
    project, records = _door_project(tmp_path, leaves=2, leaf_states=LEAVES)
    out = modify_elements(project.connect(),
                          [{"guid": "DOOR-1", "placement": _swing(0.6, -2.0, leaves="left open")}],
                          records=records)
    assert _planned(out, "DOOR-1")["placement"] == {
        "part": "Door", "hinge": "begin", "swing": "right",
        "leaves": {"state": "left open", "parameters": LEAVES["left open"]}}
    assert project.parameter_writes == [] and _writes(project) == []


def test_an_element_of_an_unlearned_part_makes_the_call_an_error(tmp_path):
    project, _ = _bed_project(tmp_path)
    changes = [{"guid": SLAB_B, "thickness": 0.25}, {"guid": OBJECT, "placement": _bed()}]
    for dry_run in (True, False):
        out = modify_elements(project.connect(), changes, dry_run=dry_run,
                              records=store(tmp_path / "empty"))
        assert list(out) == ["error"]
        assert out["error"].startswith(f"change 2 ({OBJECT}): 'Bed Double 01' has not been "
                                       "learned")
        assert "learn_library_part" in out["error"]
    assert _writes(project) == []


def test_a_record_for_another_part_makes_the_call_an_error(tmp_path):
    project = ModifyProject()
    project.set_part(OBJECT, "Bed Double 01", BED_PART)
    records = store(tmp_path, bed_record(guid="lib-1"))
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed()}],
                          records=records)
    assert "lib-1" in out["error"] and BED_PART in out["error"]
    assert "(learn_library_part)" in out["error"]


def test_a_record_that_lacks_fields_makes_the_call_an_error(tmp_path):
    project, records = _bed_project(tmp_path, head=None)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed()}],
                          records=records)
    assert "lacks head" in out["error"] and "record_library_part" in out["error"]


def test_every_change_that_cannot_be_placed_is_named(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(), [
        {"guid": OBJECT, "placement": _bed(size={"length": 2.0})},
        {"guid": SLAB, "placement": _bed()},
        {"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}], records=records)
    shape, slab, door = out["error"].split("\n")
    assert shape.startswith(f"change 1 ({OBJECT}): placement/size")
    assert "width" in shape and "placement_schema" in shape
    assert slab == (f"change 2 ({SLAB}): placement places an object, lamp, door or window; "
                    "this element is a Slab")
    assert door.startswith("change 3 (DOOR-1): ") and "'Door 29' has not been learned" in door


def test_a_field_placement_sets_given_too_makes_the_call_an_error(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "placement": _bed(),
                                               "coordinates": {"x": 0}}], records=records)
    assert out == {"error": f"change 1 ({OBJECT}): placement sets coordinates; leave one out"}
    # Another change for the same element counts too.
    out = modify_elements(project.connect(), [{"guid": OBJECT, "angle": 0.5, "layer": "Shell"},
                                              {"guid": OBJECT, "placement": _bed()}],
                          records=records)
    assert out == {"error": f"change 2 ({OBJECT}): placement sets angle; leave one out"}
    door, door_records = _door_project(tmp_path / "door")
    out = modify_elements(door.connect(), [{"guid": "DOOR-1", "oSide": False,
                                            "placement": _swing(0.6, 2.0)}],
                          records=door_records)
    assert out == {"error": "change 1 (DOOR-1): placement sets oSide; leave one out"}


def test_a_story_change_with_an_objects_placement_makes_the_call_an_error(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(), [{"guid": OBJECT, "story": 1,
                                               "placement": _bed()}], records=records)
    assert out["error"].startswith(f"change 1 ({OBJECT}): placement keeps the element's story")


def test_a_door_whose_wall_cannot_be_read_makes_the_call_an_error(tmp_path):
    project, records = _door_project(tmp_path)
    project.elements["DOOR-1"]["details"]["ownerElementId"] = {"guid": "WALL-GONE"}
    out = modify_elements(project.connect(), [{"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}],
                          records=records)
    assert out["error"].startswith("change 1 (DOOR-1): wall WALL-GONE could not be read")


def test_two_placements_for_one_element_must_agree(tmp_path):
    project, records = _door_project(tmp_path, leaves=2, leaf_states=LEAVES)
    same = {"guid": "DOOR-1", "placement": _swing(0.6, 2.0)}
    [planned] = plan_changes(project.connect(), [same, dict(same)], records=records).planned
    assert planned.item == {"reflected": True, "oSide": True}
    out = modify_elements(project.connect(),
                          [same, {"guid": "door-1",
                                  "placement": _swing(0.6, 2.0, leaves="left open")}],
                          records=records)
    assert out["error"].startswith("change 2 (DOOR-1): ")
    assert "another placement" in out["error"]


def test_a_placement_on_an_element_not_found_is_skipped_as_not_found(tmp_path):
    project, records = _bed_project(tmp_path)
    out = modify_elements(project.connect(), [{"guid": "NOPE", "placement": _bed()}],
                          records=records)
    assert "not found" in _reason(out, "NOPE")


def test_changes_without_placement_never_read_the_records(tmp_path):
    broken = tmp_path / "library-parts.json"
    broken.write_text("{not json", encoding="utf-8")
    out = modify_elements(ModifyProject().connect(), [{"guid": SLAB_B, "thickness": 0.25}],
                          records=PartRecords(broken))
    assert out["planned_count"] == 1
