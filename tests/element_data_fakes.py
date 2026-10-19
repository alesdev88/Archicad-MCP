"""A small model for get_element_data and set_element_data tests.

An Object, a Door and a Wall, with properties, classifications, GDL and marker
parameters and IFC properties. Writes change the model the way Archicad does,
including its traps: a single GDL Angle arrives in degrees and is stored in
radians, an Angle array and a marker Angle are stored as sent, and one
parameter's "Parameter Script" caps the value written to it.
"""
from __future__ import annotations

import copy
import math

from multiconn_archicad.errors import StandardAPIError

from archicad_mcp.connection import ArchicadConnection
from tests.conftest import FakeCore

ZERO = "00000000-0000-0000-0000-000000000000"
OBJ, DOOR, WALL = "obj-1", "door-1", "wall-1"
TYPES = {OBJ: "Object", DOOR: "Door", WALL: "Wall"}

SYSTEMS = [("SYS-1", "ARCHICAD Classification", "2.0"), ("SYS-2", "Uniclass", "2015")]
ITEMS = {  # guid -> (system guid, code, name)
    "ITEM-WALL": ("SYS-1", "Wall", "Wall"),
    "ITEM-DOOR": ("SYS-1", "Door", "Door"),
    "ITEM-FURN": ("SYS-1", "FURN", "Furniture"),
    "ITEM-DUP1": ("SYS-1", "DUP", "First"),
    "ITEM-DUP2": ("SYS-1", "DUP", "Second"),
    "ITEM-SS25": ("SYS-2", "Ss_25", "Wall systems"),
}

SURFACES = [{"index": 12, "name": "Paint - White", "attributeId": {"guid": "S-12"}},
            {"index": 14, "name": "Wood - Oak", "attributeId": {"guid": "S-14"}}]


def _param(name, type_, value, label=None, **extra):
    return {"name": name, "displayName": label or name, "index": 0, "type": type_,
            "value": value, "flags": extra.pop("flags", []), **extra}


def _object_params():
    return [
        _param("A", "Length", 0.9, "Width"),
        _param("B", "Length", 0.6, "Depth"),
        _param("gs_angle", "Angle", 0.0, "Rotation"),
        _param("gs_mat", "Material", 12, "Surface"),
        _param("gs_list", "Integer", 1, "Style", possibleValues=[{"value": 1}, {"value": 2}],
               canHaveCustomValue=False),
        _param("gs_capped", "Length", 0.5, "Capped"),
        _param("gs_text", "String", "old", "Note"),
        _param("gs_arr", "Angle", [0.1, 0.2], "Angles", dimension1=2, dimension2=1),
        _param("hid", "Integer", 0, "Hidden one", flags=["Hidden"]),
    ]


class FakeModel:
    def __init__(self, unavailable=(), tapir_version="1.7.1", archicad=29):
        self.unavailable = set(unavailable)
        self.tapir_version = tapir_version
        self.archicad = archicad
        self.cells = {(g, "Office/Status"): {"type": "string", "status": "normal",
                                             "value": "old"} for g in TYPES}
        self.classified = {OBJ: {"SYS-1": "ITEM-FURN"}, DOOR: {"SYS-1": "ITEM-DOOR"},
                           WALL: {"SYS-1": "ITEM-WALL"}}
        self.gdl = {OBJ: _object_params(),
                    DOOR: [_param("A", "Length", 0.9, "Width"),
                           _param("B", "Length", 2.1, "Height")]}
        self.markers = {DOOR: [_param("iNumber", "String", "001", "Number"),
                               _param("mAngle", "Angle", 0.0, "Text angle")]}
        self.ifc = {OBJ: {"Pset_FurnitureCommon.Style": "x"}, DOOR: {}, WALL: {}}
        self.raise_on: dict[str, int] = {}   # command -> raise on that call number
        self.refuse_gdl: set[str] = set()    # elements whose GDL write Tapir refuses
        self.counts: dict[str, int] = {}
        self.core = FakeCore(official=self._official(), tapir=self._tapir())

    def conn(self) -> ArchicadConnection:
        return ArchicadConnection(19723, core=self.core)

    def calls(self, command: str) -> list[dict]:
        return [p for c, p in self.core.calls if c == command]

    def _counted(self, command, handler):
        def wrapped(params):
            self.counts[command] = self.counts.get(command, 0) + 1
            if self.raise_on.get(command) == self.counts[command]:
                raise StandardAPIError(message="Invalid program status", code=4001)
            return handler(params)
        return wrapped

    # ---------- official ----------

    def _official(self):
        return {
            "API.IsAddOnCommandAvailable": lambda p: {
                "available": p["addOnCommandId"]["commandName"] not in self.unavailable},
            "API.GetProductInfo": lambda p: {"version": self.archicad},
            "API.GetTypesOfElements": lambda p: {"typesOfElements": [
                {"typeOfElement": {"elementType": TYPES[e["elementId"]["guid"]]}}
                if e["elementId"]["guid"] in TYPES
                else {"error": {"code": 7204, "message": "Element not found"}}
                for e in p["elements"]]},
            "API.GetPropertyIds": lambda p: {"properties": [
                {"propertyId": {"guid": "pid-" + (q.get("nonLocalizedName")
                                                  or "/".join(q.get("localizedName", [])))}}
                for q in p["properties"]]},
            "API.GetPropertyValuesOfElements": self._property_values,
            "API.SetPropertyValuesOfElements": self._counted(
                "API.SetPropertyValuesOfElements", self._set_properties),
            "API.GetAllClassificationSystems": {"classificationSystems": [
                {"classificationSystemId": {"guid": g}, "name": n, "version": v}
                for g, n, v in SYSTEMS]},
            "API.GetAllClassificationsInSystem": lambda p: {"classificationItems": [
                {"classificationItem": {"classificationItemId": {"guid": g}, "id": code,
                                        "name": name, "description": "", "children": []}}
                for g, (system, code, name) in ITEMS.items()
                if system == p["classificationSystemId"]["guid"]]},
            "API.GetClassificationsOfElements": self._official_classifications,
            "API.SetClassificationsOfElements": self._counted(
                "API.SetClassificationsOfElements", self._set_classifications),
        }

    def _property_values(self, p):
        rows = []
        for e in p["elements"]:
            row = []
            for q in p["properties"]:
                cell = self.cells.get((e["elementId"]["guid"],
                                       q["propertyId"]["guid"].removeprefix("pid-")))
                row.append({"propertyValue": copy.deepcopy(cell)} if cell
                           else {"error": {"code": 1, "message": "n/a"}})
            rows.append({"propertyValues": row})
        return {"propertyValuesForElements": rows}

    def _set_properties(self, p):
        for v in p["elementPropertyValues"]:
            key = (v["elementId"]["guid"], v["propertyId"]["guid"].removeprefix("pid-"))
            self.cells[key] = dict(v["propertyValue"])
        return {"executionResults": [{"success": True} for _ in p["elementPropertyValues"]]}

    def _official_classifications(self, p):
        rows = []
        for e in p["elements"]:
            ids = []
            for s in p["classificationSystemIds"]:
                system = s["classificationSystemId"]["guid"]
                one = {"classificationSystemId": {"guid": system}}
                item = self.classified.get(e["elementId"]["guid"], {}).get(system)
                if item:
                    one["classificationItemId"] = {"guid": item}
                ids.append({"classificationId": one})
            rows.append({"classificationIds": ids})
        return {"elementClassifications": rows}

    def _set_classifications(self, p):
        for c in p["elementClassifications"]:
            guid = c["elementId"]["guid"]
            cid = c["classificationId"]
            system = cid["classificationSystemId"]["guid"]
            if "classificationItemId" in cid:
                self.classified.setdefault(guid, {})[system] = cid["classificationItemId"]["guid"]
            else:
                self.classified.get(guid, {}).pop(system, None)
        return {"executionResults": [{"success": True} for _ in p["elementClassifications"]]}

    # ---------- Tapir ----------

    def _tapir(self):
        return {
            "GetAddOnVersion": lambda p: {"version": self.tapir_version},
            "FilterElements": lambda p: {"elements": [
                {"elementId": {"guid": e["elementId"]["guid"].upper()}} for e in p["elements"]]},
            "GetAllElements": lambda p: {"elements": [{"elementId": {"guid": g}} for g in TYPES]},
            "GetDetailsOfElements": lambda p: {"detailsOfElements": [
                {"type": TYPES.get(e["elementId"]["guid"], "Wall")} for e in p["elements"]]},
            "GetCurrentWindowType": {"currentWindowType": "FloorPlan"},
            "GetProjectInfo": {"isTeamwork": False},
            "GetAttributesByType": lambda p: {"attributes": SURFACES
                                              if p["attributeType"] == "Surface" else []},
            "GetClassificationsOfElements": self._tapir_classifications,
            "SetClassificationsOfElements": self._counted(
                "SetClassificationsOfElements", self._set_classifications),
            "GetGDLParametersOfElements": lambda p: {"gdlParametersOfElements": [
                {"parameters": copy.deepcopy(self.gdl[e["elementId"]["guid"]])}
                if e["elementId"]["guid"] in self.gdl
                else {"error": {"code": -2130313113,
                                "message": "Failed to get parameters of element"}}
                for e in p["elements"]]},
            "SetGDLParametersOfElements": self._counted(
                "SetGDLParametersOfElements", self._set_gdl),
            "GetMarkerParametersOfElements": lambda p: {"markerParametersOfElements": [
                {"libPart": {"name": "Marker"},
                 "parameters": copy.deepcopy(self.markers[e["elementId"]["guid"]])}
                if e["elementId"]["guid"] in self.markers
                else {"error": {"code": -2130313050, "message": (
                    f"Element with guid {e['elementId']['guid']} is not a window or a "
                    "door, only those have a marker.")}}
                for e in p["elements"]]},
            "SetMarkerParametersOfElements": self._counted(
                "SetMarkerParametersOfElements", self._set_markers),
            "GetIFCPropertiesOfElements": lambda p: {"elementIFCProperties": [
                {"elementId": {"guid": e["elementId"]["guid"].upper()}, "ifcProperties": [
                    {"propertySetName": k.split(".", 1)[0], "name": k.split(".", 1)[1],
                     "value": v} for k, v in self.ifc[e["elementId"]["guid"]].items()]}
                for e in p["elements"]]},
            "SetIFCPropertiesOfElements": self._counted(
                "SetIFCPropertiesOfElements", self._set_ifc),
        }

    def _tapir_classifications(self, p):
        rows = []
        for e in p["elements"]:
            ids = []
            for s in p["classificationSystemIds"]:
                system = s["classificationSystemId"]["guid"]
                item = self.classified.get(e["elementId"]["guid"], {}).get(system) or ZERO
                ids.append({"classificationSystemId": {"guid": system},
                            "classificationItemId": {"guid": item}})
            rows.append({"classificationIds": ids})
        return {"elementClassifications": rows}

    def _set_gdl(self, p):
        results = []
        for item in p["elementsWithGDLParameters"]:
            guid = item["elementId"]["guid"]
            if guid in self.refuse_gdl:
                results.append({"success": False, "error": {
                    "code": -2130313112, "message": f"Invalid input: bad parameter of {guid}"}})
                continue
            params = {q["name"]: q for q in self.gdl[guid]}
            for change in item["gdlParameters"]:
                target = params[change["name"]]
                value = change["value"]
                if target["type"] == "Angle" and "dimension1" not in target:
                    value = math.radians(value)       # ChangeAParameter takes degrees
                if target["name"] == "gs_capped":
                    value = min(value, 1.0)           # the Parameter Script caps it
                if isinstance(value, list):
                    target["dimension1"] = len(value)
                target["value"] = value
            results.append({"success": True})
        return {"executionResults": results}

    def _set_markers(self, p):
        for item in p["elementsWithMarkerParameters"]:
            params = {q["name"]: q for q in self.markers[item["elementId"]["guid"]]}
            for change in item["markerParameters"]:
                params[change["name"]]["value"] = change["value"]
        return {"executionResults": [{"success": True}
                                     for _ in p["elementsWithMarkerParameters"]]}

    def _set_ifc(self, p):
        for v in p["elementIFCPropertyValues"]:
            value = v["value"]
            text = ("True" if value else "False") if isinstance(value, bool) else str(value)
            self.ifc[v["elementId"]["guid"]][f"{v['propertySetName']}.{v['propertyName']}"] = text
        return {"executionResults": [{"success": True} for _ in p["elementIFCPropertyValues"]]}
