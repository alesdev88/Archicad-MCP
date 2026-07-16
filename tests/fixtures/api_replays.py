"""Canned official-API responses for a model with two walls and one zone.

Shapes follow the official JSON API documentation
(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/). If live
verification finds a mismatch, fix it HERE and in extract.py only.
"""

E = [{"elementId": {"guid": g}} for g in ("w-1", "w-2", "z-1")]

GET_ALL_ELEMENTS = {"elements": E}

GET_TYPES = {"types": [
    {"typeOfElement": {"elementId": {"guid": "w-1"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "w-2"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "z-1"}, "elementType": "Zone"}},
]}

GET_ALL_PROPERTY_NAMES = {"properties": [
    {"type": "BuiltIn", "nonLocalizedName": "General_LayerName"},
    {"type": "BuiltIn", "nonLocalizedName": "General_HomeStoryNumber"},
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


def get_property_values(parameters):
    """Values keyed (element guid, property guid). NotAvailable errors for the zone's
    wall-only props mirror real API behavior."""
    values = {
        ("w-1", "pid-General_LayerName"): "A-WALL",
        ("w-2", "pid-General_LayerName"): "Sketch",
        ("z-1", "pid-General_LayerName"): "A-ZONE",
        ("w-1", "pid-General_HomeStoryNumber"): 1,
        ("w-2", "pid-General_HomeStoryNumber"): 2,
        ("z-1", "pid-General_HomeStoryNumber"): 1,
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
                row.append({"propertyValue": {"value": values[key], "status": "normal"}})
            else:
                row.append({"error": {"code": 1, "message": "Property not available"}})
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

TAPIR = {
    "GetIFCPropertiesOfElements": TAPIR_IFC_PROPERTIES,
    "GetProjectInfo": {"projectName": "Test House", "untitled": False, "teamwork": False},
    "GetAddOnVersion": {"version": "1.8.2"},
}
