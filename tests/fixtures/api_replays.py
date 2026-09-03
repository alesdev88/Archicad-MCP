"""Canned official-API responses for a model with two walls and one zone.

Shapes follow the official JSON API documentation
(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/). If live
verification finds a mismatch, fix it HERE and in extract.py only.
"""

E = [{"elementId": {"guid": g}} for g in ("w-1", "w-2", "z-1")]

GET_ALL_ELEMENTS = {"elements": E}

ELEMENT_TYPES = {"w-1": "Wall", "w-2": "Wall", "z-1": "Zone"}


# Live-verified key: the API returns "typesOfElements" (not "types").
def get_types(parameters):
    """Answers for the requested elements only, so chunked calls stay honest."""
    return {"typesOfElements": [
        {"typeOfElement": {"elementId": el["elementId"],
                           "elementType": ELEMENT_TYPES[el["elementId"]["guid"]]}}
        for el in parameters["elements"]]}

GET_ALL_PROPERTY_NAMES = {"properties": [
    {"type": "BuiltIn", "nonLocalizedName": "ModelView_LayerName"},
    {"type": "BuiltIn", "nonLocalizedName": "Zone_ZoneNumber"},
    {"type": "BuiltIn", "nonLocalizedName": "Zone_ZoneName"},
    {"type": "UserDefined", "localizedName": ["OFFICE", "Fire Rating"]},
]}


def get_property_ids(parameters):
    """Echo one propertyId per requested property, guid derived from its name."""
    out = []
    for p in parameters["properties"]:
        key = p.get("nonLocalizedName") or "/".join(p.get("localizedName", []))
        out.append({"propertyId": {"guid": f"pid-{key}"}})
    return {"properties": out}


# Property pairs that are genuinely NOT applicable to the element. The API
# returns an error cell (no type) for these. Everything else is available and
# carries a type even when unset (live-verified: an unset property comes back
# as {"type": ..., "status": "userUndefined"} with no value).
_UNAVAILABLE = {
    ("z-1", "pid-OFFICE/Fire Rating"),   # wall-only property on a zone
    ("z-1", "pid-OFFICE/Status"),
}


def get_property_values(parameters):
    """Values keyed (element guid, property guid).

    Mirrors the live AC 29 shape: cells carry `type` and `status` alongside
    `value`; unset-but-available properties still report their type.
    """
    values = {
        ("w-1", "pid-ModelView_LayerName"): "A-WALL",
        ("w-2", "pid-ModelView_LayerName"): "Sketch",
        ("z-1", "pid-ModelView_LayerName"): "A-ZONE",
        ("w-1", "pid-OFFICE/Fire Rating"): "EI60",
        ("z-1", "pid-Zone_ZoneNumber"): "101",
        ("z-1", "pid-Zone_ZoneName"): "Office",
    }
    result = []
    for el in parameters["elements"]:
        row = []
        for prop in parameters["properties"]:
            key = (el["elementId"]["guid"], prop["propertyId"]["guid"])
            if key in values:
                row.append({"propertyValue": {"type": "string", "status": "normal",
                                              "value": values[key]}})
            elif key in _UNAVAILABLE:
                row.append({"error": {"code": 1, "message": "Property not available"}})
            else:
                row.append({"propertyValue": {"type": "string",
                                              "status": "userUndefined"}})
        result.append({"propertyValues": row})
    return {"propertyValuesForElements": result}


GET_CLASSIFICATION_SYSTEMS = {"classificationSystems": [
    {"classificationSystemId": {"guid": "cs-1"}, "name": "ARCHICAD Classification",
     "version": "2.0"},
]}


def get_classifications(parameters):
    by_guid = {"w-1": {"classificationId": {"guid": "c-wall"}},
               "w-2": None, "z-1": {"classificationId": {"guid": "c-zone"}}}
    result = []
    for el in parameters["elements"]:
        item = by_guid[el["elementId"]["guid"]]
        # Live shape: the item is "classificationItemId", omitted when the
        # element is unclassified in the system.
        one = {"classificationSystemId": {"guid": "cs-1"}}
        if item:
            one["classificationItemId"] = item["classificationId"]
        result.append({"classificationIds": [{"classificationId": one}]})
    return {"elementClassifications": result}


_ATTRIBUTE_IDS = {"Layer": ["layer-1", "layer-2"], "Line": ["line-1"]}


def get_attributes_by_type(parameters):
    """Ids per attribute type; a type the fixture project has none of answers []."""
    return {"attributeIds": [{"attributeId": {"guid": g}}
                             for g in _ATTRIBUTE_IDS.get(parameters["attributeType"], [])]}


GET_ATTRIBUTES_BY_TYPE = get_attributes_by_type

GET_LAYER_ATTRIBUTES = {"attributes": [
    {"layerAttribute": {"attributeId": {"guid": "layer-1"}, "name": "A-WALL"}},
    {"layerAttribute": {"attributeId": {"guid": "layer-2"}, "name": "A-ZONE"}},
]}

TAPIR_IFC_PROPERTIES = {"elements": [
    {"elementId": {"guid": "w-1"},
     "properties": [{"propertySetName": "Pset_WallCommon",
                     "name": "FireRating", "value": "EI60"}]},
    {"elementId": {"guid": "w-2"}, "properties": []},
    {"elementId": {"guid": "z-1"}, "properties": []},
]}

OFFICIAL = {
    "API.GetProductInfo": {"version": 29, "buildNumber": 5003, "languageCode": "INT"},
    "API.IsAddOnCommandAvailable": {"available": True},
    "API.GetAllElements": GET_ALL_ELEMENTS,
    "API.GetTypesOfElements": get_types,
    "API.GetAllPropertyNames": GET_ALL_PROPERTY_NAMES,
    "API.GetPropertyIds": get_property_ids,
    "API.GetPropertyValuesOfElements": get_property_values,
    "API.GetAllClassificationSystems": GET_CLASSIFICATION_SYSTEMS,
    "API.GetClassificationsOfElements": get_classifications,
    "API.GetAttributesByType": GET_ATTRIBUTES_BY_TYPE,
    "API.GetLayerAttributes": GET_LAYER_ATTRIBUTES,
}

# Shape verified against live AC 29.0: each detail carries floorIndex + layerIndex.
def get_details_of_elements(parameters):
    floors = {"w-1": 0, "w-2": 1, "z-1": 0}
    return {"detailsOfElements": [
        {"floorIndex": floors.get(el["elementId"]["guid"]), "layerIndex": 3,
         "type": "Wall"}
        for el in parameters["elements"]]}


TAPIR = {
    "GetIFCPropertiesOfElements": TAPIR_IFC_PROPERTIES,
    "GetDetailsOfElements": get_details_of_elements,
    "GetProjectInfo": {"projectName": "Test House", "isUntitled": False,
                       "isTeamwork": False,
                       "projectLocation": "/Users/tester/Test House.pln",
                       "projectPath": "/Users/tester/Test House.pln"},
    "GetAddOnVersion": {"version": "1.8.2"},
    # Tapir sees the whole plan; the official API.GetAllElements sees model
    # elements only. This fixture model has no 2D elements, so both agree --
    # tests/test_element_coverage.py builds a plan where they differ.
    "GetAllElements": GET_ALL_ELEMENTS,
    "GetSelectedElements": {"elements": []},
    "GetElementsByType": lambda p: {"elements": [
        {"elementId": {"guid": g}} for g, t in ELEMENT_TYPES.items()
        if t == p["elementType"]]},
}


# ---------- definitions (live shapes, AC 29 / Tapir 1.5.9, 2026-09-03) ----------

# Tapir GetAllProperties: one entry per definition, built-in and custom alike.
TAPIR_ALL_PROPERTIES = {"properties": [
    {"propertyId": {"guid": "pid-ModelView_LayerName"}, "propertyType": "StaticBuiltIn",
     "propertyGroupName": "Model View", "propertyName": "Layer Name",
     "propertyCollectionType": "Single", "propertyValueType": "String",
     "propertyMeasureType": "Default", "propertyIsEditable": True, "isExpressionBased": False},
    {"propertyId": {"guid": "11111111-1111-1111-1111-111111111111"}, "propertyType": "DynamicBuiltIn",
     "propertyGroupName": "Geometry", "propertyName": "Wall Height",
     "propertyCollectionType": "Single", "propertyValueType": "Real",
     "propertyMeasureType": "Length", "propertyIsEditable": False, "isExpressionBased": False},
    {"propertyId": {"guid": "pid-OFFICE/Fire Rating"}, "propertyType": "Custom",
     "propertyGroupName": "OFFICE", "propertyName": "Fire Rating",
     "propertyCollectionType": "Single", "propertyValueType": "String",
     "propertyMeasureType": "Default", "propertyIsEditable": True, "isExpressionBased": False},
    {"propertyId": {"guid": "pid-OFFICE/Status"}, "propertyType": "Custom",
     "propertyGroupName": "OFFICE", "propertyName": "Status",
     "propertyCollectionType": "SingleChoiceEnumeration", "propertyValueType": "String",
     "propertyMeasureType": "Default", "propertyIsEditable": True, "isExpressionBased": False,
     "possibleEnumValues": [{"enumValue": {"displayValue": "Approved", "nonLocalizedValue": "Approved",
                                           "guid": "00000000-0000-0000-0000-000000000000"}},
                            {"enumValue": {"displayValue": "Draft", "nonLocalizedValue": "Draft",
                                           "guid": "00000000-0000-0000-0000-000000000000"}}]},
]}


def get_property_definition_availability(parameters):
    """Custom definitions list the classification items they apply to
    (expanded: parents and children both present); built-ins list nothing."""
    avail = {"pid-OFFICE/Fire Rating": ["c-wall"], "pid-OFFICE/Status": ["c-wall"]}
    return {"propertyDefinitionAvailabilityList": [
        {"propertyDefinitionAvailability": {
            "propertyId": p["propertyId"],
            "availableClassifications": [{"classificationItemId": {"guid": g}}
                                         for g in avail.get(p["propertyId"]["guid"], [])]}}
        for p in parameters["propertyIds"]]}


# Tree of the one system: Building > (Wall, Slab); Site.
CLASSIFICATION_TREE = {"classificationItems": [
    {"classificationItem": {"classificationItemId": {"guid": "c-building"}, "id": "Building",
                            "name": "", "description": "", "children": [
        {"classificationItem": {"classificationItemId": {"guid": "c-wall"}, "id": "Wall",
                                "name": "", "description": ""}},
        {"classificationItem": {"classificationItemId": {"guid": "c-slab"}, "id": "Slab",
                                "name": "", "description": ""}},
    ]}},
    {"classificationItem": {"classificationItemId": {"guid": "c-zone"}, "id": "Zone",
                            "name": "", "description": ""}},
]}

GET_LINE_ATTRIBUTES = {"attributes": [
    {"lineAttribute": {"attributeId": {"guid": "line-1"}, "name": "Dashed"}}]}

OFFICIAL.update({
    "API.GetPropertyDefinitionAvailability": get_property_definition_availability,
    "API.GetAllClassificationsInSystem": CLASSIFICATION_TREE,
    "API.GetDetailsOfProperties": lambda p: {"propertyDefinitions": [
        {"propertyDefinition": {"propertyId": x["propertyId"],
                                "group": {"name": x["propertyId"]["guid"].split("/")[0].replace("pid-", "")},
                                "name": x["propertyId"]["guid"].split("/")[-1],
                                "isEditable": True, "type": "string"}}
        for x in p["properties"]]},
    "API.GetLineAttributes": GET_LINE_ATTRIBUTES,
})
TAPIR["GetAllProperties"] = TAPIR_ALL_PROPERTIES
