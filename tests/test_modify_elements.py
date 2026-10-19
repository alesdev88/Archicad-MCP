"""modify_elements planning and dry run, against a fake Tapir built from the
answers recorded on MCP-Test (tests/fixtures/modify_elements)."""
import copy

from archicad_mcp.core.modify import modify_elements, plan_changes
from tests.fixtures.modify_project import ModifyProject

SLAB = "05C7A02F-4251-6F40-8407-67893BEFF6AC"     # SLA - 001, story 0, layer 33
SLAB2 = "8BD33B9C-5B7D-5C46-8B28-9EC1CB9310CC"    # SLA - 002, story 1, z 3.25
OBJECT = "5349C8FC-A4BF-3548-A614-77BEB2CE4261"   # FU - 003, story 0, layer 3
MESH = "D4DB880C-8083-F749-B27F-0886B2D6044B"     # TR - 005, story 1, layer 45
SLAB_B = "SLAB-B"                                 # SLA - 101, Basic, story 0, layer 33

WRITES = ("Modify", "SetDetails", "Move", "SetSuspend", "Lock", "Unlock")


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
