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
            "BuildingMaterial": [{"index": 2, "name": "Concrete", "guid": "BM-CONCRETE"}],
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
        return self.window == "FloorPlan" and guid in self.elements

    def _mine(self, guid: str) -> bool:
        return not self.teamwork or self.mine is None or guid in self.mine

    def _editable(self, guid: str) -> bool:
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
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def _write_handlers(self) -> dict:
        return {}


__all__ = ["ModifyProject", "TapirCommandError"]
