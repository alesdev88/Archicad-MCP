"""Definition editing: address resolution, plans, sending. No property VALUE is
ever read here; the fake core has no GetPropertyValuesOfElements on purpose."""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, editing_unavailable, group_by_error)
from tests.fixtures.definition_edit_replays import fake_editing_core


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def test_resolve_item_by_system_and_code():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA/40.10") == (["i-40-10"], None)


def test_branch_suffix_takes_the_item_and_everything_below():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/40.20/*")
    assert err is None
    assert guids == ["i-40-20", "i-40-20-1"]


def test_longest_system_name_wins():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA 2/40") == (["j-40"], None)
    assert index.resolve("ELEA/40") == (["i-40"], None)


def test_unknown_code_is_an_error_naming_the_address():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/99")
    assert guids == [] and "ELEA/99" in err


def test_a_guid_resolves_to_itself():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("i-40") == (["i-40"], None)


def test_label_and_siblings():
    index = ClassificationIndex.load(_conn()[0])
    assert index.label("i-40-20-1") == "ELEA/40.20.1"
    assert index.siblings("i-40-10") == ["i-40-20"]


def test_old_tapir_is_refused_before_anything_is_sent():
    conn, core = _conn(marker=False)
    result = editing_unavailable(conn)
    assert result is not None and "UpdateClassificationItems" in result["error"]
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


def test_failures_group_by_message():
    failed = [{"target": "ELEA/Sifra", "message": "no access right"},
              {"target": "ELEA/Kategorija", "message": "no access right"},
              {"target": "ELEA/Dolzina", "message": "name already used"}]
    groups = group_by_error(failed)
    assert groups[0] == {"message": "no access right", "count": 2,
                         "sample": ["ELEA/Sifra", "ELEA/Kategorija"]}


# ---------- Task 9: property definitions and the non-enum plan ----------

from archicad_mcp.core.definition_edit import Definitions, plan_property_change


def _defs():
    conn, _ = _conn()
    return Definitions.load(conn), ClassificationIndex.load(conn)


def test_resolve_property_whose_group_contains_a_slash():
    defs, _ = _defs()
    p, err = defs.resolve("A/B/C")
    assert err is None and p.guid == "p-slash"


def test_builtin_properties_are_refused():
    defs, index = _defs()
    plan = plan_property_change({"property": "b-layer", "name": "X"}, defs, index)
    assert plan.errors == ["built-in properties cannot be edited"]


def test_rename_and_move_group():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Sifra opreme",
                                 "group": "ELEA Oprema"}, defs, index)
    assert plan.errors == []
    assert plan.payload == {"propertyId": {"guid": "p-code"}, "name": "Sifra opreme",
                            "groupId": {"guid": "g-ELEA Oprema"}}
    assert plan.changes == {"name": ["Sifra", "Sifra opreme"], "group": ["ELEA", "ELEA Oprema"]}


def test_rename_onto_an_existing_address_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Kategorija"}, defs, index)
    assert plan.errors == ["'ELEA/Kategorija' already exists"]


def test_missing_group_is_an_error_not_a_create():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "group": "Nope"}, defs, index)
    assert "no custom property group 'Nope'" in plan.errors[0]


def test_unknown_field_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "colour": "red"}, defs, index)
    assert "unknown field" in plan.errors[0]


def test_plain_default_is_type_checked():
    defs, index = _defs()
    ok = plan_property_change({"property": "ELEA/Dolzina", "default": 2.5}, defs, index)
    assert ok.payload["defaultValue"] == {"basicDefaultValue": {
        "status": "normal", "type": "length", "value": 2.5}}
    bad = plan_property_change({"property": "ELEA/Dolzina", "default": "long"}, defs, index)
    assert "takes a number" in bad.errors[0]


def test_default_none_means_undefined():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "default": None}, defs, index)
    assert plan.payload["defaultValue"] == {"basicDefaultValue": {"status": "userUndefined"}}


def test_expressions_replace_the_default():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Povrsina",
                                 "expressions": ["{Property:Volume}"]}, defs, index)
    assert plan.payload["defaultValue"] == {"expressions": ["{Property:Volume}"]}
    assert plan.changes["default"] == [{"expressions": ["{Property:Area}"]},
                                       {"expressions": ["{Property:Volume}"]}]


def test_default_and_expressions_together_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Povrsina", "default": 1,
                                 "expressions": ["1"]}, defs, index)
    assert plan.errors == ["send default or expressions, not both"]


def test_availability_add_branch_and_remove_warns():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "availability": {
        "add": ["ELEA/40.20/*"], "remove": ["ELEA/40.10"]}}, defs, index)
    assert plan.errors == []
    assert plan.payload["availability"] == {
        "add": [{"classificationItemId": {"guid": g}} for g in ["i-40-20", "i-40-20-1"]],
        "remove": [{"classificationItemId": {"guid": "i-40-10"}}]}
    assert plan.changes["availability"] == {"added": ["ELEA/40.20", "ELEA/40.20.1"],
                                            "removed": ["ELEA/40.10"]}
    assert any("not applicable" in w for w in plan.warnings)


def test_availability_set_with_add_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "availability": {
        "set": ["ELEA/40"], "add": ["ELEA/40.10"]}}, defs, index)
    assert plan.errors == ["availability takes either set, or add and/or remove"]


def test_nothing_to_change_is_reported_not_sent():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Sifra"}, defs, index)
    assert plan.payload == {"propertyId": {"guid": "p-code"}}
    assert plan.warnings == ["nothing to change"]
