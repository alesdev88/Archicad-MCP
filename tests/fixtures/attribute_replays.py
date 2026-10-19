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


# ---------- writes, the way Tapir 1.7.1's AttributeCommands.cpp does them ----------

CREATE_COMMANDS = {
    "CreateLayers": ("Layer", "layerDataArray"),
    "CreateLayerCombinations": ("LayerCombination", "layerCombinationDataArray"),
    "CreateLines": ("Line", "lineDataArray"),
    "CreateFills": ("Fill", "fillDataArray"),
    "CreateZoneCategories": ("ZoneCategory", "zoneCategoryDataArray"),
    "CreatePenTables": ("PenTable", "penTableDataArray"),
    "CreateProfiles": ("Profile", "profileDataArray"),
    "CreateBuildingMaterials": ("BuildingMaterial", "buildingMaterialDataArray"),
    "CreateComposites": ("Composite", "compositeDataArray"),
    "CreateSurfaces": ("Surface", "surfaceDataArray"),
}
ATTREXIST = {"code": -2130312915, "message": "Already exists."}
COMPOSITE_EXISTS = {"code": -7003, "message": "Composite already exists."}
REFUSED = -2130312311


class WritableStore(AttributeStore):
    """AttributeStore that also answers Create*, DeleteAttributes and the
    attribute property commands. Each Create keeps what Tapir keeps and loses
    what Tapir loses (the danger list in the design), so a test can show the
    tool puts the lost parts back."""

    def __init__(self, records=None, properties=None, available=None):
        super().__init__(records)
        # (attribute guid, property guid) -> display string
        self.values: dict[tuple[str, str], str] = dict(properties or {})
        # pairs the property is available for
        self.available: set[tuple[str, str]] = set(available or self.values)
        self.created: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def by_name(self, attr_type, name):
        return next((r for r in self.records.get(attr_type, []) if r["name"] == name), None)

    def creator(self, command):
        attr_type, key = CREATE_COMMANDS[command]

        def answer(params):
            overwrite = params.get("overwriteExisting", False)
            out = []
            for item in params[key]:
                out.append(self._one(attr_type, item, overwrite))
            return {"attributeIds": out}
        return answer

    def _one(self, attr_type, item, overwrite):
        name = item["name"]
        if overwrite:
            guid = (item.get("attributeId") or {}).get("guid")
            existing = self.by_guid(attr_type, guid) if guid else self.by_name(attr_type, name)
        else:
            existing = self.by_name(attr_type, name)
            if existing is not None:
                return {"error": COMPOSITE_EXISTS if attr_type == "Composite" else ATTREXIST}
        if existing is None:
            if self.by_name(attr_type, name) is not None:
                return {"error": {"code": -1, "message": "Failed to create."}}
            guid = G(f"new-{attr_type}-{name}")
            record = {"attributeId": {"guid": guid},
                      "index": max([r["index"] for r in self.records[attr_type]] or [0]) + 1,
                      "name": name}
            self._apply(attr_type, record, item, created=True)
            self.records[attr_type].append(record)
            self.created.append((attr_type, name))
            return {"attributeId": {"guid": guid}}
        if existing["index"] == 1 and attr_type in ("Layer", "Line", "Fill"):
            return {"error": {"code": REFUSED, "message": "Failed to modify."}}
        clash = self.by_name(attr_type, name)
        if clash is not None and clash is not existing:
            return {"error": {"code": -2130312988, "message": "Failed to modify."}}
        existing["name"] = name
        self._apply(attr_type, existing, item, created=False)
        return {"attributeId": existing["attributeId"]}

    def _apply(self, attr_type, record, item, created):
        fields = {k: copy.deepcopy(v) for k, v in item.items()
                  if k not in ("attributeId", "index", "name")}
        if attr_type == "Composite":
            # Tapir clears useWith and rebuilds skins and separators from the request.
            record["useWith"] = fields.pop("useWith", [])
        if attr_type == "BuildingMaterial" and not created:
            # Archicad 29 and 30 keep them (live 19.10.2026); the store wipes them
            # anyway, so the tests show the tool does not depend on that.
            for text in ("id", "manufacturer", "description"):
                record[text] = fields.pop(text, "")
        if attr_type == "Line" and "lineType" not in fields:
            for ignored in ("period", "height", "dashItems", "lineItems"):
                fields.pop(ignored, None)
        if attr_type == "LayerCombination" and "layers" in fields:
            listed = {e["attributeId"]["guid"]: {"attributeId": e["attributeId"],
                                                 "isHidden": e.get("isHidden", False),
                                                 "isLocked": e.get("isLocked", False),
                                                 "isWireframe": e.get("isWireframe", False),
                                                 "intersectionGroupNr":
                                                     e.get("intersectionGroupNr", 1)}
                      for e in fields.pop("layers")}
            keep = [] if created else [e for e in record.get("layers", [])
                                       if e["attributeId"]["guid"] not in listed]
            record["layers"] = keep + list(listed.values())
        if attr_type == "PenTable" and "pens" in fields:
            pens = {p["index"]: p for p in record.get("pens", [])}
            for pen in fields.pop("pens"):
                pens.setdefault(pen["index"], {"index": pen["index"]}).update(pen)
            record["pens"] = [pens[i] for i in sorted(pens)]
        if attr_type == "Surface" and "texture" in fields:
            texture = fields.pop("texture")
            current = record.setdefault("texture", {})
            for k, v in texture.items():
                # Flags can only be switched on.
                current[k] = (current.get(k, False) or v) if isinstance(v, bool) else v
        if attr_type == "Profile" and "skinOverrides" in fields:
            for override in fields.pop("skinOverrides"):
                for skin in record.get("skins", []):
                    if skin["skinId"] == override["skinId"]:
                        skin.update({k: v for k, v in override.items() if k != "skinId"})
        record.update(fields)

    def delete(self, params):
        results = []
        for item in params["attributesToDelete"]:
            attr_type = item["attributeType"]
            guid = item["attributeId"]["attributeId"]["guid"]
            record = self.by_guid(attr_type, guid)
            if record is None:
                results.append({"success": False, "error": {
                    "code": -2130313115, "message": "Attribute not found."}})
            elif record["index"] == 1:
                results.append({"success": False, "error": {
                    "code": REFUSED, "message": "Failed to delete."}})
            else:
                self.records[attr_type].remove(record)
                self.deleted.append((attr_type, record["name"]))
                results.append({"success": True})
        return {"executionResults": results}

    def get_property_values(self, params):
        out = []
        for a in params["attributeIds"]:
            guid = a["attributeId"]["guid"]
            values = []
            for p in params["properties"]:
                key = (guid, p["propertyId"]["guid"])
                if key in self.available:
                    values.append({"propertyValue": {"value": self.values.get(key, "")}})
                else:
                    # Live on AC 29 and AC 30 (19.10.2026).
                    values.append({"error": {"code": -2130312908,
                                             "message": "Not available or not evaluated property"}})
            out.append({"propertyValues": values})
        return {"propertyValuesForAttributes": out}

    def set_property_values(self, params):
        results = []
        for item in params["attributePropertyValues"]:
            key = (item["attributeId"]["guid"], item["propertyId"]["guid"])
            if key not in self.available:
                results.append({})
                continue
            self.values[key] = item["propertyValue"]["value"]
            results.append({"success": True})
        return {"executionResults": results}

    def tapir_table(self) -> dict:
        table = super().tapir_table()
        table.update({c: self.creator(c) for c in CREATE_COMMANDS})
        table["DeleteAttributes"] = self.delete
        table["GetPropertyValuesOfAttributes"] = self.get_property_values
        table["SetPropertyValuesOfAttributes"] = self.set_property_values
        return table
