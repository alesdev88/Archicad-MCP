"""Attributes as Tapir 1.7.1 answers for them, trimmed from live reads of the
MCP-Test project (AC 29/5101, 19.10.2026), and a store that answers the
attribute commands the way Tapir's AttributeCommands.cpp does.

GUIDs are shortened to readable fakes; the shapes are the live ones.
"""
from __future__ import annotations

import copy
import uuid

from multiconn_archicad.errors import TapirCommandError


def G(label: str) -> str:
    """A real-format GUID for a readable label (Tapir's schemas check the format)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"attr:{label}")).upper()


def _id(label: str) -> dict:
    return {"attributeId": {"guid": G(label)}}


# type -> [records as the type's Get* command answers them]
RECORDS: dict[str, list[dict]] = {
    "Layer": [
        {**_id("lay-1"), "index": 1, "name": "\u0014", "isHidden": False,
         "isLocked": False, "isWireframe": False, "intersectionGroupNr": 1},
        {**_id("lay-2"), "index": 2, "name": "Interior - Partition", "isHidden": False,
         "isLocked": False, "isWireframe": False, "intersectionGroupNr": 1},
        {**_id("lay-3"), "index": 3, "name": "Interior - Furniture ", "isHidden": False,
         "isLocked": False, "isWireframe": False, "intersectionGroupNr": 1},
    ],
    "LayerCombination": [
        {**_id("lc-1"), "index": 1, "name": "02 Drafting", "layers": [
            {**_id("lay-1"), "isHidden": False, "isLocked": False, "isWireframe": False,
             "intersectionGroupNr": 1},
            {**_id("lay-2"), "isHidden": True, "isLocked": False, "isWireframe": False,
             "intersectionGroupNr": 1}]},
    ],
    "Line": [
        {**_id("lin-1"), "index": 1, "name": "Solid Line", "scaleWithPlan": False,
         "defineScale": 1, "lineType": "Solid", "period": 0},
        {**_id("lin-2"), "index": 2, "name": "Dotted Dense", "scaleWithPlan": False,
         "defineScale": 1, "lineType": "Dashed", "period": 1.763888836,
         "dashItems": [{"dash": 0, "gap": 1.763888836}]},
    ],
    "Fill": [
        {**_id("fil-1"), "index": 1, "name": "Empty Fill", "subType": "Empty"},
        {**_id("fil-2"), "index": 2, "name": "Face Brick", "subType": "Vector",
         "scaleWithPlan": False, "useForWalls": True, "useForDraft": True,
         "useForCover": False, "horizontalSpacing": 0.0009, "verticalSpacing": 0.0009,
         "angle": 0.785398163, "bitPattern": "993366CC993366CC", "percent": 0,
         "lineItems": [{"frequency": 1, "direction": 0, "offsetLine": 0,
                        "offset": {"x": 0, "y": 0}}]},
        {**_id("fil-5"), "index": 5, "name": "Concrete Block", "subType": "Vector"},
    ],
    "ZoneCategory": [
        {**_id("zc-2"), "index": 2, "name": "Other Usage", "categoryCode": "7",
         "color": {"red": 0.490196078, "green": 0.647058824, "blue": 0.611764706},
         "stampName": "Zone Stamp 02", "stampMainGuid": "ED7BFA7A-DC0F-43B3-97BC-D2AA1B4F7365",
         "stampRevGuid": "62D2FBCE-95A5-4ABD-B0D1-EBF21CEF1512"},
    ],
    "PenTable": [
        {**_id("pt-11"), "index": 11, "name": "07 Mechanical", "isActiveForModel": False,
         "isActiveForLayout": False,
         "pens": [{"index": i, "color": {"red": 0, "green": 0, "blue": 0},
                   "width": 0.13, "description": f"Pen {i}"} for i in range(1, 256)]},
    ],
    "Profile": [
        {**_id("pro-2"), "index": 2, "name": "Brick Veneer Block Wall", "wallType": True,
         "beamType": False, "coluType": False, "handrailType": False,
         "otherGDLObjectType": False, "useWith": ["Wall"], "width": 0.287, "height": 2.7,
         "skins": [{"skinId": "F3B62A00", "buildingMaterialId": _id("bm-8"),
                    "fillId": _id("fil-2"), "contourPen": 148,
                    "contourLineTypeId": _id("lin-1"),
                    "outlineCoords": [{"x": 0, "y": 0}, {"x": 0.075, "y": 0}],
                    "isCore": False, "isFinish": True,
                    "edges": [{"buildingMaterialId": _id("bm-8"), "pen": 148,
                               "lineTypeId": _id("lin-1")}]}]},
    ],
    "BuildingMaterial": [
        {**_id("bm-8"), "index": 8, "name": "Concrete Block - Structural", "id": "ST-05",
         "manufacturer": "", "description": "", "connPriority": 750, "cutFillIndex": 5,
         "cutFillPen": 169, "cutFillBackgroundPen": -1, "cutSurfaceIndex": 132,
         "cutFillOrientation": "ProjectOrigin", "thermalConductivity": 0.6,
         "density": 1400, "heatCapacity": 880, "embodiedEnergy": 0.72,
         "embodiedCarbon": 0.088, "showUncutLines": True, "collisionDetection": True},
        {**_id("bm-9"), "index": 9, "name": "Plaster", "id": "", "manufacturer": "",
         "description": "", "connPriority": 100, "cutFillIndex": 2, "cutFillPen": 1,
         "cutFillBackgroundPen": -1, "cutSurfaceIndex": 132,
         "cutFillOrientation": "ProjectOrigin", "thermalConductivity": 0.5,
         "density": 1300, "heatCapacity": 1000, "embodiedEnergy": 0,
         "embodiedCarbon": 0, "showUncutLines": True, "collisionDetection": True},
    ],
    "Composite": [
        {**_id("com-4"), "index": 4, "name": "100 Block Plastered", "useWith": ["Wall"],
         "skins": [{"type": "Finish", "framePen": 148, "thickness": 0.012,
                    "buildingMaterialId": _id("bm-9")},
                   {"type": "Core", "framePen": 149, "thickness": 0.1,
                    "buildingMaterialId": _id("bm-8")}],
         "separators": [{"linePen": 148, "lineTypeId": _id("lin-1")},
                        {"linePen": 149, "lineTypeId": _id("lin-1")},
                        {"linePen": 148, "lineTypeId": _id("lin-1")}]},
    ],
    "Surface": [
        {**_id("sur-132"), "index": 132, "name": "Stucco - White Rough",
         "materialType": "General", "ambientReflection": 75, "diffuseReflection": 95,
         "specularReflection": 8, "transparency": 0, "shine": 0,
         "transparencyAttenuation": 0, "emissionAttenuation": 0,
         "surfaceColor": {"red": 1, "green": 1, "blue": 1},
         "specularColor": {"red": 0.99, "green": 0.97, "blue": 0.95},
         "emissionColor": {"red": 0, "green": 0, "blue": 0},
         "fillId": _id("fil-2"),
         "texture": {"name": "Stucco", "rotationAngle": 0, "xSize": 0.6, "ySize": 0.45}},
    ],
}

# Get command -> (type, input key, output key). GetLayerCombinations is the odd
# one: input "attributes", and each answer wrapped in {"layerCombination": ...}
# with "attributeIndex" for "index" (live, 19.10.2026).
GET_COMMANDS = {
    "GetLayers": ("Layer", "attributeIds", "layers"),
    "GetLayerCombinations": ("LayerCombination", "attributes", "layerCombinations"),
    "GetLines": ("Line", "attributeIds", "lines"),
    "GetFills": ("Fill", "attributeIds", "fills"),
    "GetZoneCategories": ("ZoneCategory", "attributeIds", "zoneCategories"),
    "GetPenTables": ("PenTable", "attributeIds", "penTables"),
    "GetProfiles": ("Profile", "attributeIds", "profiles"),
    "GetBuildingMaterials": ("BuildingMaterial", "attributeIds", "buildingMaterials"),
    "GetComposites": ("Composite", "attributeIds", "composites"),
    "GetSurfaces": ("Surface", "attributeIds", "surfaces"),
}

NOT_FOUND = {"error": {"code": -2130313113, "message": "Failed to retrieve attribute."}}


class AttributeStore:
    """Answers GetAttributesByType and the Get* commands from RECORDS."""

    def __init__(self, records: dict[str, list[dict]] | None = None):
        self.records = copy.deepcopy(records or RECORDS)

    def by_guid(self, attr_type: str, guid: str) -> dict | None:
        return next((r for r in self.records.get(attr_type, [])
                     if r["attributeId"]["guid"] == guid), None)

    def get_by_type(self, params: dict) -> dict:
        attr_type = params["attributeType"]
        if attr_type not in self.records:
            raise TapirCommandError(message=f"unknown type {attr_type}", code=-1)
        return {"attributes": [{"attributeId": r["attributeId"], "index": r["index"],
                                "name": r["name"], "modificationTime": 0}
                               for r in self.records[attr_type]]}

    def getter(self, command: str):
        attr_type, in_key, out_key = GET_COMMANDS[command]

        def answer(params: dict) -> dict:
            out = []
            for item in params[in_key]:
                record = self.by_guid(attr_type, item["attributeId"]["guid"])
                if record is None:
                    out.append(NOT_FOUND)
                elif command == "GetLayerCombinations":
                    wrapped = {k: v for k, v in record.items() if k != "index"}
                    out.append({"layerCombination": {**wrapped,
                                                     "attributeIndex": record["index"]}})
                else:
                    out.append(copy.deepcopy(record))
            return {out_key: out}
        return answer

    def tapir_table(self) -> dict:
        table = {"GetAttributesByType": self.get_by_type}
        table.update({c: self.getter(c) for c in GET_COMMANDS})
        return table
