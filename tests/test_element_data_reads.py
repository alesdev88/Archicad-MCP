"""get_element_data reads every kind through one address syntax."""
from archicad_mcp.core.element_data import WILDCARD_ELEMENT_LIMIT, get_element_data
from tests.element_data_fakes import DOOR, OBJ, WALL, FakeModel


def _read(model, guids, properties, **kwargs):
    return get_element_data(model.conn(), guids, properties, **kwargs)


def _one(model, guid, properties):
    [element] = _read(model, [guid], properties)["elements"]
    return element


def test_a_property_only_call_keeps_its_shape():
    element = _one(FakeModel(), OBJ, ["Office/Status"])
    assert element == {"guid": OBJ, "type": "Object", "layer": None,
                       "properties": {"Office/Status": "old"}}


def test_gdl_parameters_read_by_name_any_case():
    element = _one(FakeModel(), OBJ, ["gdl:A", "gdl:gs_ANGLE", "gdl:gs_arr", "gdl:nope"])
    assert element["properties"] == {"gdl:A": 0.9, "gdl:gs_ANGLE": 0.0,
                                     "gdl:gs_arr": [0.1, 0.2], "gdl:nope": None}
    assert "labels" not in element and "unreadable" not in element


def test_a_gdl_wildcard_lists_the_visible_parameters_with_their_labels():
    element = _one(FakeModel(), OBJ, ["gdl:*"])
    assert "gdl:hid" not in element["properties"]
    assert element["properties"]["gdl:gs_mat"] == 12
    assert element["labels"]["gdl:A"] == "Width"
    assert set(element["labels"]) <= set(element["properties"])


def test_an_element_without_gdl_parameters_says_why():
    element = _one(FakeModel(), WALL, ["gdl:A"])
    assert element["properties"] == {"gdl:A": None}
    assert "Failed to get parameters" in element["unreadable"]["gdl"]


def test_marker_parameters_of_a_door():
    element = _one(FakeModel(), DOOR, ["marker:*"])
    assert element["properties"] == {"marker:iNumber": "001", "marker:mAngle": 0.0}
    assert element["labels"]["marker:iNumber"] == "Number"


def test_marker_parameters_of_a_wall_are_unreadable():
    element = _one(FakeModel(), WALL, ["marker:iNumber"])
    assert "only those have a marker" in element["unreadable"]["marker"]


def test_ifc_properties_by_name_and_wildcard():
    model = FakeModel()
    assert _one(model, OBJ, ["ifc:Pset_FurnitureCommon.Style"])["properties"] == \
        {"ifc:Pset_FurnitureCommon.Style": "x"}
    assert _one(model, OBJ, ["ifc:*"])["properties"] == {"ifc:Pset_FurnitureCommon.Style": "x"}


def test_classification_addresses_read_the_item_id():
    element = _one(FakeModel(), DOOR, ["classification:ARCHICAD Classification",
                                       "classification:Uniclass"])
    assert element["properties"] == {"classification:ARCHICAD Classification": "Door",
                                     "classification:Uniclass": None}


def test_a_classification_wildcard_reads_every_system():
    element = _one(FakeModel(), WALL, ["classification:*"])
    assert element["properties"] == {"classification:ARCHICAD Classification": "Wall",
                                     "classification:Uniclass": None}


def test_include_classifications_still_answers_item_guids():
    [element] = _read(FakeModel(), [WALL], [], include_classifications=True)["elements"]
    assert element["classifications"]["ARCHICAD Classification"] == "ITEM-WALL"


def test_an_unknown_classification_system_is_an_error_naming_the_systems():
    out = _read(FakeModel(), [WALL], ["classification:Nope"])
    assert "Nope" in out["error"] and "Uniclass" in out["error"]


def test_without_the_tapir_command_the_kind_is_unreadable_with_a_hint():
    model = FakeModel(unavailable={"GetMarkerParametersOfElements"})
    element = _one(model, DOOR, ["marker:iNumber", "Office/Status"])
    assert element["properties"] == {"marker:iNumber": None, "Office/Status": "old"}
    assert "1.7.1" in element["unreadable"]["marker"]


def test_a_malformed_address_is_an_error():
    assert "ifc:<Pset>.<Name>" in _read(FakeModel(), [OBJ], ["ifc:bad"])["error"]


def test_a_wildcard_is_refused_for_many_elements():
    out = _read(FakeModel(), [OBJ] * (WILDCARD_ELEMENT_LIMIT + 1), ["gdl:*"])
    assert str(WILDCARD_ELEMENT_LIMIT) in out["error"]


def test_kinds_are_read_only_when_asked_for():
    model = FakeModel()
    _read(model, [OBJ], ["Office/Status"])
    assert not model.calls("GetGDLParametersOfElements")
    assert not model.calls("GetIFCPropertiesOfElements")
    assert not model.calls("API.GetAllClassificationSystems")
