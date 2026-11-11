"""get_element_data reads every kind through one address syntax."""
from archicad_mcp import extract
from archicad_mcp.connection import ArchicadConnection
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


def test_a_blank_address_reads_none_as_before():
    element = _one(FakeModel(), OBJ, ["", "Office/Status"])
    assert element["properties"] == {"": None, "Office/Status": "old"}


# --- properties the official API cannot read: Tapir 2.0.0, or the reason ---

def _refused_model(version="2.0.0"):
    """label-1 is outside the official read (a 2D element); Tapir answers by `version`."""
    from archicad_mcp import extract
    from tests import test_property_fallback as fb

    layer = {"propertyId": {"guid": f"PID-{extract.BUILTIN_LAYER.upper()}"},
             "propertyValueType": "String", "propertyCollectionType": "Single",
             "propertyMeasureType": "Default"}
    core = fb._fake(
        official_rows={"label-1": fb._REFUSED_ELEMENT},
        version=version,
        definitions={"properties": [*fb._DEFINITIONS["properties"], layer]},
        values=lambda params: {"propertyValuesForElements": [
            {"propertyValues": [
                {"propertyValue": {"value": {**fb._TAPIR_VALUES,
                                             f"pid-{extract.BUILTIN_LAYER}": "A-ANNO"}[
                    p["propertyId"]["guid"]]}, "isAvailable": True}
                for p in params["properties"]]} for _ in params["elements"]]})
    core.official_responses["API.GetTypesOfElements"] = lambda params: {"typesOfElements": [
        {"typeOfElement": {"elementType": "Label"}} for _ in params["elements"]]}
    return core


def _read_refused(version="2.0.0"):
    core = _refused_model(version)
    conn = ArchicadConnection(19723, core=core)
    return get_element_data(conn, ["label-1"], ["General_Locked"])["elements"][0]


def test_a_2d_element_property_comes_through_tapir():
    item = _read_refused()
    assert item["properties"]["General_Locked"] is True
    assert item["layer"] == "A-ANNO"
    assert "unreadable" not in item


def test_without_tapir_2_the_reason_is_given_instead_of_a_value():
    item = _read_refused(version="1.8.0")
    assert item["properties"]["General_Locked"] is None
    assert item["layer"] is None
    assert item["unreadable"] == {"General_Locked": extract.NOT_READ_ELEMENT,
                                  "layer": extract.NOT_READ_ELEMENT}


def test_an_official_answer_is_returned_unchanged_beside_a_gap():
    from tests import test_property_fallback as fb
    core = _refused_model()
    core.official_responses["API.GetPropertyValuesOfElements"] = lambda params: {
        "propertyValuesForElements": [
            {"propertyValues": [fb._ANSWERED, fb._ANSWERED]} for _ in params["elements"]]}
    item = get_element_data(ArchicadConnection(19723, core=core), ["label-1"],
                            ["General_Locked"])["elements"][0]
    assert item["properties"]["General_Locked"] is False
    assert "unreadable" not in item
