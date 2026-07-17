"""Canned official-API responses for a model with two walls and one zone.

Shapes follow the official JSON API documentation
(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/). If live
verification finds a mismatch, fix it HERE and in extract.py only.
"""

E = [{"elementId": {"guid": g}} for g in ("w-1", "w-2", "z-1")]

GET_ALL_ELEMENTS = {"elements": E}

# Live-verified key: the API returns "typesOfElements" (not "types").
GET_TYPES = {"typesOfElements": [
    {"typeOfElement": {"elementId": {"guid": "w-1"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "w-2"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "z-1"}, "elementType": "Zone"}},
]}

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


# Property pairs that are genuinely NOT applicable to the element — the API
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
        one = {"classificationSystemId": {"guid": "cs-1"}}
        if item:
            one["classificationId"] = item["classificationId"]
        result.append({"classificationIds": [{"classificationId": one}]})
    return {"elementClassifications": result}


GET_ATTRIBUTES_BY_TYPE = {"attributeIds": [{"attributeId": {"guid": "layer-1"}},
                                           {"attributeId": {"guid": "layer-2"}}]}

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
    "API.GetTypesOfElements": GET_TYPES,
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
    "GetProjectInfo": {"projectName": "Test House", "untitled": False, "teamwork": False},
    "GetAddOnVersion": {"version": "1.8.2"},
}
