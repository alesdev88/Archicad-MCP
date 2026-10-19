"""list_attributes: all ten attribute types, and details=true from Tapir's Get*
commands with attribute references shown by name (the vocabulary
edit_attributes takes back)."""
import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.attributes import list_attributes
from tests.conftest import FakeCore
from tests.fixtures.attribute_replays import RECORDS, AttributeStore, G

OFFICIAL_DETAIL = {"Layer": "API.GetLayerAttributes", "Line": "API.GetLineAttributes",
                   "Fill": "API.GetFillAttributes", "Composite": "API.GetCompositeAttributes",
                   "Surface": "API.GetSurfaceAttributes",
                   "LayerCombination": "API.GetLayerCombinationAttributes",
                   "ZoneCategory": "API.GetZoneCategoryAttributes",
                   "Profile": "API.GetProfileAttributes", "PenTable": "API.GetPenTableAttributes",
                   "BuildingMaterial": "API.GetBuildingMaterialAttributes"}


def _official(store):
    def ids(p):
        return {"attributeIds": [{"attributeId": r["attributeId"]}
                                 for r in store.records[p["attributeType"]]]}
    table = {"API.GetAttributesByType": ids,
             "API.IsAddOnCommandAvailable": lambda p: {"available": True}}
    for attr_type, command in OFFICIAL_DETAIL.items():
        key = attr_type[0].lower() + attr_type[1:] + "Attribute"
        table[command] = (lambda t, k: lambda p: {"attributes": [
            {k: {"name": store.by_guid(t, a["attributeId"]["guid"])["name"]}}
            for a in p["attributeIds"]]})(attr_type, key)
    return table


def make(missing=()):
    store = AttributeStore()
    official = _official(store)
    official["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] not in missing}
    tapir = store.tapir_table()
    tapir["GetAddOnVersion"] = {"version": "1.7.1"}
    return ArchicadConnection(19723, core=FakeCore(official=official, tapir=tapir)), store


@pytest.mark.parametrize("given,expected", [
    ("Lines", "Line"), ("fill", "Fill"), ("layer combinations", "LayerCombination"),
    ("PenTables", "PenTable"), ("building_material", "BuildingMaterial")])
def test_every_type_and_its_plural_lists_names(given, expected):
    conn, _ = make()
    out = list_attributes(conn, given)
    assert out["attribute_type"] == expected
    assert out["names"] == [r["name"] for r in RECORDS[expected]]


def test_an_unknown_type_lists_the_ten():
    conn, _ = make()
    error = list_attributes(conn, "Pen")["error"]
    assert "PenTable" in error and "LayerCombination" in error


def test_details_show_composite_references_by_name():
    conn, _ = make()
    [composite] = list_attributes(conn, "Composite", details=True)["attributes"]
    assert [s["buildingMaterialId"] for s in composite["skins"]] == [
        "Plaster", "Concrete Block - Structural"]
    assert composite["separators"][0]["lineTypeId"] == "Solid Line"
    assert composite["skins"][1]["thickness"] == 0.1


def test_details_show_index_references_by_name():
    conn, _ = make()
    out = list_attributes(conn, "BuildingMaterial", details=True, names=["Concrete Block - Structural"])
    [material] = out["attributes"]
    assert material["cutFillIndex"] == "Concrete Block"
    assert material["cutSurfaceIndex"] == "Stucco - White Rough"


def test_details_unwrap_layer_combinations():
    conn, _ = make()
    [combination] = list_attributes(conn, "LayerCombination", details=True)["attributes"]
    assert combination["index"] == 1 and combination["name"] == "02 Drafting"
    assert combination["layers"][1] == {"attributeId": "Interior - Partition",
                                        "isHidden": True, "isLocked": False,
                                        "isWireframe": False, "intersectionGroupNr": 1}


def test_details_name_profile_skin_and_edge_references():
    conn, _ = make()
    [profile] = list_attributes(conn, "Profile", details=True)["attributes"]
    skin = profile["skins"][0]
    assert skin["fillId"] == "Face Brick" and skin["contourLineTypeId"] == "Solid Line"
    assert skin["edges"][0]["buildingMaterialId"] == "Concrete Block - Structural"


def test_a_reference_to_a_missing_attribute_says_so():
    conn, store = make()
    store.records["Composite"][0]["skins"][0]["buildingMaterialId"] = {
        "attributeId": {"guid": G("gone")}}
    [composite] = list_attributes(conn, "Composite", details=True)["attributes"]
    assert composite["skins"][0]["buildingMaterialId"] == f"<missing BuildingMaterial {G('gone')}>"


def test_unknown_names_are_reported():
    conn, _ = make()
    out = list_attributes(conn, "Layer", details=True, names=["Interior - Partition", "Nope"])
    assert [a["name"] for a in out["attributes"]] == ["Interior - Partition"]
    assert out["not_found"] == ["Nope"]


def test_details_page_heavy_types_by_default():
    conn, store = make()
    store.records["PenTable"] = [dict(store.records["PenTable"][0], name=f"T{i}",
                                      attributeId={"guid": G(f"pt-{i}")}, index=i)
                                 for i in range(5)]
    out = list_attributes(conn, "PenTable", details=True)
    assert out["total"] == 5 and len(out["attributes"]) == 2 and out["next_offset"] == 2
    out = list_attributes(conn, "PenTable", details=True, offset=4)
    assert len(out["attributes"]) == 1 and "next_offset" not in out


def test_details_need_the_types_get_command():
    conn, _ = make(missing={"GetComposites"})
    error = list_attributes(conn, "Composite", details=True)["error"]
    assert "GetComposites" in error and "1.5.4" in error
