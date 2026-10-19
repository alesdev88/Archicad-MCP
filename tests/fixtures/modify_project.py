"""A fake Archicad + Tapir for the modify_elements tests.

Element details, layers, stories and the group answers are the ones recorded
on MCP-Test (tests/fixtures/modify_elements), plus schema-derived details for
the types MCP-Test does not have. Editability follows the rules core.editability
reads: Archicad changes only elements of the active window's database, and
FilterElements IsEditable is false for a hidden layer, an unreserved Teamwork
element and whatever `not_editable` names (hotlinks, locked layers).
"""
from __future__ import annotations

import copy

from multiconn_archicad.errors import TapirCommandError

from archicad_mcp.connection import ArchicadConnection
from tests.conftest import FakeCore
from tests.fixtures import api_replays
from tests.fixtures.modify_elements import all_details, layers_answer, load

BAD_ID = -2130313115

# The top-level array of each Modify command, as the bundled definitions name it.
LIST_KEYS = {
    "ModifyWalls": "wallsWithDetails", "ModifyBeams": "beamsWithDetails",
    "ModifySlabs": "slabsWithDetails", "ModifyColumns": "columnsWithDetails",
    "ModifyWindows": "windowsWithDetails", "ModifyDoors": "doorsWithDetails",
    "ModifyMorphs": "morphsWithDetails", "ModifyRoofs": "roofsWithDetails",
    "ModifyMeshes": "meshesData", "ModifyObjects": "objectsWithDetails",
    "ModifyLamps": "lampsWithDetails", "ModifyTexts": "textsWithDetails",
    "ModifyLabels": "labelsWithDetails",
}


class ModifyProject:
    def __init__(self, window: str = "FloorPlan", version: str = "1.7.1",
                 tapir: bool = True):
        self.elements: dict[str, dict] = all_details()
        self.window = window
        self.version = version
        self.tapir = tapir
        self.not_editable: set[str] = set()
        self.hidden: set[str] = set()
        self.teamwork = False
        self.mine: set[str] | None = None
        self.locked_property: set[str] = set()
        self.locked_layers: set[int] = set()
        self.groups: dict[str, str] = {}
        self.suspend = False
        self.suspend_history: list[bool] = []
        self.missing_commands: set[str] = set()
        # Write behaviour.
        self.ignore_draw: set[str] = set()          # draw order steps that do nothing
        self.fail_setdetails: dict[str, dict] = {}  # GUID -> error
        self.fail: dict[tuple[str, str], dict] = {}  # (command, GUID) -> error
        self.partial: set[str] = set()              # applied, then answered failure
        self.raise_on: set[str] = set()             # commands that raise
        self.mate_changes: dict[str, dict] = {}     # group mate -> entry keys a Modify sets
        self.dead_after: str | None = None          # this command, and every call after it, raises
        self._dead = False
        self.suspend_fail: set[bool] = set()        # SetSuspendGroupsMode answers failure for these
        # Elements FilterElements IsEditable passes but the details read does not
        # find: floor-plan labels seen from a Layout (28.09.2026). Seen from the
        # floor plan, layout Drawings and section-only Objects are found by the
        # details read too (19.10.2026), so that case is not this one.
        self.other_database: set[str] = set()
        self.stories = load("stories.json")
        self.attributes: dict[str, list[dict]] = {
            "Composite": [
                {"index": 1, "name": "Generic", "guid": "A8836EE9-5C72-492B-A7F6-30EFED26B3EE"},
                {"index": 5, "name": "Slab 250", "guid": "C0000000-0000-0000-0000-000000000250"}],
            "Surface": [
                {"index": 7, "name": "Paint - White", "guid": "5A000000-0000-0000-0000-000000000007"}],
            "Line": [
                {"index": 1, "name": "Solid Line", "guid": "28A7C677-9234-418F-9E4D-D10C22581DF6"},
                {"index": 4, "name": "Dashed", "guid": "11111111-0000-0000-0000-000000000004"}],
            "BuildingMaterial": [{"index": 2, "name": "Concrete", "guid": "B0000000-0000-0000-0000-0000000C0C0E"}],
            "Fill": [{"index": 3, "name": "Background", "guid": "1D87AE9A-3C74-4A1A-BE7C-56B7651F0F3C"}],
            "Profile": [],
        }
        self.core: FakeCore | None = None

    # ---------- helpers for tests ----------

    def attribute_guid(self, attribute_type: str, name: str) -> str:
        [guid] = [a["guid"] for a in self.attributes[attribute_type] if a["name"] == name]
        return guid

    def sent(self, command: str) -> list[dict]:
        return [params for name, params in self.core.calls if name == command]

    def command_names(self) -> list[str]:
        return [name for name, _ in self.core.calls]

    # ---------- state rules ----------

    def _in_database(self, guid: str) -> bool:
        return (self.window == "FloorPlan" and guid in self.elements
                and guid not in self.other_database)

    def _mine(self, guid: str) -> bool:
        return not self.teamwork or self.mine is None or guid in self.mine

    def _editable(self, guid: str) -> bool:
        if guid in self.other_database:
            return True
        return (self._in_database(guid) and guid not in self.not_editable
                and guid not in self.hidden and self._mine(guid))

    # ---------- read handlers ----------

    def filter_elements(self, p):
        tests = {"IsEditable": self._editable,
                 "IsVisibleByLayer": lambda g: self._in_database(g) and g not in self.hidden,
                 "InMyWorkspace": lambda g: self._in_database(g) and self._mine(g)}
        [name] = p["filters"]
        return {"elements": [e for e in p["elements"] if tests[name](e["elementId"]["guid"])]}

    def details(self, p):
        fields = p.get("fields")
        out = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            if not self._in_database(guid):
                out.append({"error": {"code": BAD_ID,
                                      "message": "Failed to get the details of element"}})
                continue
            entry = copy.deepcopy(self.elements[guid])
            if fields is not None:
                entry = {k: v for k, v in entry.items() if k in fields}
            out.append(entry)
        return {"detailsOfElements": out}

    def attributes_by_type(self, p):
        kind = p["attributeType"]
        if kind == "Layer":
            return load("layers_by_type.json")
        return {"attributes": [{"attributeId": {"guid": a["guid"]}, "index": a["index"],
                                "name": a["name"]} for a in self.attributes.get(kind, [])]}

    def groups_of(self, p):
        return {"groupGuids": [
            {"groupId": {"guid": self.groups[e["elementId"]["guid"]]}}
            if e["elementId"]["guid"] in self.groups
            else copy.deepcopy(load("groups.json")["ungrouped"])
            for e in p["elements"]]}

    def elements_of_groups(self, p):
        return {"elementsOfGroups": [
            {"elements": [{"elementId": {"guid": g}} for g, group in self.groups.items()
                          if group == item["groupId"]["guid"]]}
            for item in p["groups"]]}

    def command_available(self, p):
        name = p["addOnCommandId"]["commandName"]
        return {"available": self.tapir and name not in self.missing_commands}

    def property_ids(self, p):
        return {"properties": [{"propertyId": {"guid": "pid-" + (q.get("nonLocalizedName") or "")}}
                               for q in p["properties"]]}

    def property_values(self, p):
        return {"propertyValuesForElements": [
            {"propertyValues": [{"propertyValue": {
                "type": "boolean", "status": "normal",
                "value": e["elementId"]["guid"] in self.locked_property}}
                for _ in p["properties"]]}
            for e in p["elements"]]}

    def _tapir_handlers(self) -> dict:
        return {
            "GetAddOnVersion": lambda p: {"version": self.version},
            "GetDetailsOfElements": self.details,
            "FilterElements": self.filter_elements,
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
            "GetProjectInfo": lambda p: {**api_replays.TAPIR["GetProjectInfo"],
                                         "isTeamwork": self.teamwork},
            "GetStories": lambda p: copy.deepcopy(self.stories),
            "GetAttributesByType": self.attributes_by_type,
            "GetLayers": layers_answer(locked=self.locked_layers),
            "GetGroupsOfElements": self.groups_of,
            "GetElementsOfGroups": self.elements_of_groups,
            "GetSuspendGroupsMode": lambda p: {"suspendGroups": self.suspend},
        }

    def connect(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official.update({
            "API.IsAddOnCommandAvailable": self.command_available,
            "API.GetPropertyIds": self.property_ids,
            "API.GetPropertyValuesOfElements": self.property_values,
        })
        tapir = dict(api_replays.TAPIR)
        tapir.update(self._tapir_handlers())
        tapir.update(self._write_handlers())
        official = {name: self._mortal(name, h) for name, h in official.items()}
        tapir = {name: self._mortal(name, h) for name, h in tapir.items()}
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def _mortal(self, name: str, handler):
        """The handler, raising once the connection has died (dead_after)."""
        def call(p):
            if name == self.dead_after:
                self._dead = True
            if self._dead:
                raise TapirCommandError(message="Invalid program status (ongoing user input)",
                                        code=4001)
            return handler(p) if callable(handler) else handler
        return call

    # ---------- write handlers ----------

    def _raise_if(self, command: str) -> None:
        if command in self.raise_on:
            raise TapirCommandError(message=f"{command} timed out", code=None)

    def _level(self, index) -> float:
        return {s["index"]: float(s["level"]) for s in self.stories["stories"]}.get(index, 0.0)

    def _shift_absolute(self, entry: dict, dz: float) -> None:
        details = entry.get("details", {})
        if "zCoordinate" in details:
            details["zCoordinate"] += dz
        origin = details.get("origin")
        if isinstance(origin, dict) and "z" in origin:
            origin["z"] += dz

    def set_details(self, p):
        self._raise_if("SetDetailsOfElements")
        items = p["elementsWithDetails"]

        def needs_host(item):
            # Tapir's pre-scan (ElementCommands.cpp): doors, windows and
            # skylights without a draw order of 1 to 7 run first.
            entry = self.elements.get(item["elementId"]["guid"], {})
            order = item["details"].get("drawIndex", -1)
            return entry.get("type") in ("Door", "Window", "Skylight") and not 0 < order <= 7

        ordered = [i for i in items if needs_host(i)] + [i for i in items if not needs_host(i)]
        results = []
        for item in ordered:
            guid = item["elementId"]["guid"]
            if guid in self.fail_setdetails:
                results.append({"success": False, "error": self.fail_setdetails[guid]})
                continue
            entry, details = self.elements[guid], item["details"]
            if "floorIndex" in details:
                # Keeps the height above the story, so it moves with the story.
                shift = self._level(details["floorIndex"]) - self._level(entry["floorIndex"])
                entry["floorIndex"] = details["floorIndex"]
                self._shift_absolute(entry, shift)
            if "layerIndex" in details:
                entry["layerIndex"] = details["layerIndex"]
            if "drawIndex" in details and guid not in self.ignore_draw:
                entry["drawIndex"] = details["drawIndex"] or entry["drawIndex"]
            results.append({"success": True})
        return {"executionResults": results}

    # Where a written field shows in the details, where the name differs.
    _WRITE_KEYS = {("ModifyWalls", "thickness"): "begThickness",
                   ("ModifyObjects", "coordinates"): "origin",
                   ("ModifyLamps", "coordinates"): "origin"}

    def _apply(self, command: str, guid: str, fields: dict) -> None:
        details = self.elements[guid]["details"]
        for name, value in fields.items():
            details[self._WRITE_KEYS.get((command, name), name)] = copy.deepcopy(value)
        group = self.groups.get(guid)
        for mate, patch in self.mate_changes.items():
            if mate != guid and self.groups.get(mate) == group:
                self.elements[mate].update(copy.deepcopy(patch))

    def modify(self, command: str, list_key: str):
        def handler(p):
            self._raise_if(command)
            results = []
            for item in p[list_key]:
                guid = item["elementId"]["guid"]
                fields = item.get("meshData") if "meshData" in item else \
                    {k: v for k, v in item.items() if k != "elementId"}
                if (command, guid) in self.fail:
                    results.append({"success": False, "error": self.fail[(command, guid)]})
                    continue
                self._apply(command, guid, fields)
                if guid in self.partial:
                    results.append({"success": False,
                                    "error": {"code": 2, "message": "second step failed"}})
                else:
                    results.append({"success": True})
            return {"executionResults": results}
        return handler

    def move(self, p):
        self._raise_if("MoveElements")
        results = []
        for item in p["elementsWithMoveVectors"]:
            guid = item["elementId"]["guid"]
            if ("MoveElements", guid) in self.fail:
                results.append({"success": False, "error": self.fail[("MoveElements", guid)]})
                continue
            self._shift_absolute(self.elements[guid], item["moveVector"]["z"])
            results.append({"success": True})
        return {"executionResults": results}

    def set_suspend(self, p):
        # Tapir answers a refusal as executionResult success false, without
        # raising (ElementGroupingCommands.cpp).
        self._raise_if("SetSuspendGroupsMode")
        if p["suspendGroups"] in self.suspend_fail:
            return {"executionResult": {"success": False,
                                        "error": {"code": 3, "message": "not now"}}}
        self.suspend = p["suspendGroups"]
        self.suspend_history.append(self.suspend)
        return {"executionResult": {"success": True}}

    def _write_handlers(self) -> dict:
        handlers = {"SetDetailsOfElements": self.set_details, "MoveElements": self.move,
                    "SetSuspendGroupsMode": self.set_suspend}
        for command, list_key in LIST_KEYS.items():
            handlers[command] = self.modify(command, list_key)
        return handlers


