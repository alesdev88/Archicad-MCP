"""library-parts-loaded and ifc-spatial-structure, with what they read from Archicad.

Shapes as Tapir's main after 1.8.0 answers: GetMissingLibPartNames gives the part's name
per element, whether or not its library is loaded; GetIFCSpatialStructure the project,
site and building it reports, and the story of each element asked about.
"""
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import build_snapshot
from archicad_mcp.rules.builtin.ifc_structure import IfcSpatialStructureRule
from archicad_mcp.rules.builtin.library_parts import LibraryPartsLoadedRule
from archicad_mcp.rules.engine import data_needs, ifc_story_types, run_rules
from archicad_mcp.rules.loader import RULE_TYPES
from archicad_mcp.rules.types import IfcStructure, MissingParts, ModelSnapshot, RuleConfigError
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

CHAIR, LAMP, DOOR = "CHAIR-1", "LAMP-1", "DOOR-1"
W1, W2 = "WALL-1", "WALL-2"


def part(name, index, kind="Object"):
    return {"guid": f"lib-{index}", "index": index, "documentName": name,
            "fileName": f"{name}.gsm", "typeId": kind}


class Project:
    def __init__(self, types=None, names=None, library=("Chair 01", "Door"), unread=(),
                 ifc=None, ifc_stories=None, floors=None, missing=()):
        self.types = types if types is not None else {
            CHAIR: "Object", LAMP: "Lamp", DOOR: "Door"}
        self.names = names if names is not None else {
            CHAIR: "Chair 01", LAMP: "Old Lamp", DOOR: "Door"}
        self.library = library
        self.unread = list(unread)
        self.ifc = ifc if ifc is not None else {
            "project": {}, "site": {}, "building": {}}
        self.ifc_stories = ifc_stories or {}
        self.floors = floors or {}
        self.missing = set(missing)

    def conn(self):
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing}
        tapir = dict(api_replays.TAPIR)
        tapir["GetElementsByType"] = lambda p: {"elements": [
            {"elementId": {"guid": g}} for g, t in self.types.items() if t == p["elementType"]]}
        tapir["GetMissingLibPartNames"] = self._names
        tapir["GetAvailableLibraryParts"] = self._parts
        tapir["GetIFCSpatialStructure"] = self._ifc
        tapir["FilterElements"] = self._filter
        tapir["GetStories"] = {"stories": [{"index": i} for i in range(3)]}
        tapir["GetDetailsOfElements"] = lambda p: {"detailsOfElements": [
            {"floorIndex": self.floors.get(e["elementId"]["guid"])} for e in p["elements"]]}
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def _filter(self, p):
        # By story, as Tapir 1.7.3 and newer answers the home story without a details read.
        return {"elements": [e for e in p["elements"]
                             if self.floors.get(e["elementId"]["guid"]) in p["floorIndices"]]}

    def _names(self, p):
        out = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            name = self.names.get(guid)
            out.append({"elementId": {"guid": guid}, "libPartName": name} if name is not None
                       else {"error": {"code": 1, "message": "Failed to get the name"}})
        return {"libPartNames": out}

    def _parts(self, p):
        total = len(self.library) + len(self.unread)
        indices = [i for i in range(1, total + 1) if i not in self.unread]
        return {"libraryParts": [part(n, i) for n, i in zip(self.library, indices)],
                "skippedCount": len(self.unread)}

    def _ifc(self, p):
        answer = dict(self.ifc)
        if p and p.get("elements"):
            answer["elements"] = [
                {"elementId": e["elementId"], "storyIndex": self.ifc_stories[e["elementId"]["guid"]]}
                if e["elementId"]["guid"] in self.ifc_stories
                else {"error": {"code": 1, "message": "no story"}}
                for e in p["elements"]]
        return answer


def parts_snapshot(project):
    return build_snapshot(project.conn(), frozenset({"library_parts"}))


def parts_rule(**extra):
    return LibraryPartsLoadedRule.from_config(
        {"id": "parts", "type": "library-parts-loaded", **extra})


def ifc_rule(**extra):
    return IfcSpatialStructureRule.from_config(
        {"id": "ifc", "type": "ifc-spatial-structure", **extra})


def ifc_snapshot(project, rule):
    return build_snapshot(project.conn(), data_needs([rule]),
                          ifc_story_types=ifc_story_types([rule]))


# ---------- library-parts-loaded ----------

def test_both_types_load_from_yaml_names():
    assert RULE_TYPES["library-parts-loaded"] is LibraryPartsLoadedRule
    assert RULE_TYPES["ifc-spatial-structure"] is IfcSpatialStructureRule


def test_it_fails_on_elements_whose_part_is_not_loaded():
    result = parts_rule().check(parts_snapshot(Project()))
    assert result.passed is False
    assert result.failing_guids == (LAMP,)
    assert result.failure_count == 1
    assert "1 of 3" in result.message


def test_the_message_names_no_part():
    # A verdict holds rule ids, counts and failing GUIDs, never model data.
    assert "Old Lamp" not in parts_rule().check(parts_snapshot(Project())).message


def test_it_passes_when_every_part_is_loaded():
    project = Project(names={CHAIR: "Chair 01", DOOR: "Door"},
                      types={CHAIR: "Object", DOOR: "Door"})
    result = parts_rule().check(parts_snapshot(project))
    assert result.passed is True and result.failing_guids == ()


@pytest.mark.parametrize("spelling", ["chair 01", "Chair 01.gsm", "CHAIR 01"])
def test_another_spelling_of_a_loaded_part_is_not_missing(spelling):
    project = Project(types={CHAIR: "Object"}, names={CHAIR: spelling})
    assert parts_rule().check(parts_snapshot(project)).passed is True


def test_it_asks_for_objects_lamps_windows_and_doors_only():
    project = Project(types={CHAIR: "Object", W1: "Wall"}, names={CHAIR: "Chair 01"})
    conn = project.conn()
    build_snapshot(conn, frozenset({"library_parts"}))
    asked = [p["elementType"] for c, p in project.core.calls if c == "GetElementsByType"]
    assert asked == ["Object", "Lamp", "Window", "Door"]
    sent = [p for c, p in project.core.calls if c == "GetMissingLibPartNames"]
    assert sent == [{"elements": [{"elementId": {"guid": CHAIR}}]}]


def test_a_project_with_no_such_elements_lists_no_library():
    project = Project(types={W1: "Wall"})
    conn = project.conn()
    snapshot = build_snapshot(conn, frozenset({"library_parts"}))
    assert snapshot.missing_parts == MissingParts(checked=0, guids=())
    assert "GetAvailableLibraryParts" not in [c for c, _ in project.core.calls]


def test_an_element_whose_name_cannot_be_read_does_not_fail_it():
    project = Project(types={CHAIR: "Object", LAMP: "Lamp"}, names={CHAIR: "Chair 01"})
    result = parts_rule().check(parts_snapshot(project))
    assert result.passed is True
    assert "1" in result.message and "could not be read" in result.message


def test_an_empty_name_is_not_a_missing_part():
    project = Project(types={CHAIR: "Object"}, names={CHAIR: ""})
    assert parts_rule().check(parts_snapshot(project)).passed is True


def test_it_skips_when_some_library_entries_could_not_be_read():
    # An unread entry among the listed ones could be the part: no false failure.
    project = Project(library=("Chair 01", "Door"), unread=[2])
    result = parts_rule().check(parts_snapshot(project))
    assert result.skipped is True
    assert "could not be read" in result.skip_reason


def test_unread_entries_past_the_last_part_are_no_parts():
    # Live 21.10.2026: 123 indices after 7402 parts.
    project = Project(library=("Chair 01", "Door"), unread=[3, 4])
    result = parts_rule().check(parts_snapshot(project))
    assert result.skipped is False and result.failing_guids == (LAMP,)


def test_it_skips_on_a_tapir_without_the_command():
    result = parts_rule().check(parts_snapshot(Project(missing=["GetMissingLibPartNames"])))
    assert result.skipped is True and "GetMissingLibPartNames" in result.skip_reason


def test_it_skips_when_archicad_refuses_the_read():
    project = Project()
    conn = project.conn()
    del project.core.tapir_responses["GetMissingLibPartNames"]
    result = parts_rule().check(build_snapshot(conn, frozenset({"library_parts"})))
    assert result.skipped is True


# ---------- ifc-spatial-structure ----------

def test_it_passes_when_project_site_and_building_are_reported():
    rule = ifc_rule()
    result = rule.check(ifc_snapshot(Project(), rule))
    assert result.passed is True and result.failure_count == 0


@pytest.mark.parametrize("left_out, name", [("project", "IfcProject"), ("site", "IfcSite"),
                                            ("building", "IfcBuilding")])
def test_it_fails_on_what_archicad_does_not_report(left_out, name):
    ifc = {k: {} for k in ("project", "site", "building") if k != left_out}
    rule = ifc_rule()
    result = rule.check(ifc_snapshot(Project(ifc=ifc), rule))
    assert result.passed is False and result.failure_count == 1
    assert name in result.message
    assert result.failing_guids == ()


def test_without_element_types_no_element_is_read():
    project = Project()
    conn = project.conn()
    rule = ifc_rule()
    build_snapshot(conn, data_needs([rule]), ifc_story_types=ifc_story_types([rule]))
    assert [p for c, p in project.core.calls if c == "GetIFCSpatialStructure"] == [{}]
    assert "GetElementsByType" not in [c for c, _ in project.core.calls]


def test_element_types_compare_the_ifc_story_with_the_home_story():
    project = Project(types={W1: "Wall", W2: "Wall", CHAIR: "Object"},
                      ifc_stories={W1: 0, W2: 2}, floors={W1: 0, W2: 1})
    rule = ifc_rule(element_types=["Wall"])
    result = rule.check(ifc_snapshot(project, rule))
    assert result.passed is False
    assert result.failing_guids == (W2,) and result.failure_count == 1


def test_an_element_whose_story_cannot_be_read_does_not_fail_it():
    project = Project(types={W1: "Wall"}, ifc_stories={}, floors={W1: 0})
    rule = ifc_rule(element_types=["Wall"])
    result = rule.check(ifc_snapshot(project, rule))
    assert result.passed is True and "could not be read" in result.message


def test_each_rule_compares_only_its_own_types():
    wall_rule = ifc_rule(element_types=["Wall"])
    door_rule = IfcSpatialStructureRule.from_config(
        {"id": "doors", "type": "ifc-spatial-structure", "element_types": ["Door"]})
    project = Project(types={W1: "Wall", DOOR: "Door"}, ifc_stories={W1: 2, DOOR: 0},
                      floors={W1: 0, DOOR: 0})
    snapshot = build_snapshot(project.conn(), data_needs([wall_rule, door_rule]),
                              ifc_story_types=ifc_story_types([wall_rule, door_rule]))
    assert wall_rule.check(snapshot).failing_guids == (W1,)
    assert door_rule.check(snapshot).failing_guids == ()


def test_a_misspelt_element_type_is_a_load_error():
    with pytest.raises(RuleConfigError, match="element_types"):
        ifc_rule(element_types=["Wal"])


def test_it_skips_on_a_tapir_without_the_command():
    rule = ifc_rule()
    result = rule.check(ifc_snapshot(Project(missing=["GetIFCSpatialStructure"]), rule))
    assert result.skipped is True and "GetIFCSpatialStructure" in result.skip_reason


def test_it_skips_where_archicad_refuses_it():
    # Before Archicad 28 Tapir answers with a top-level error, which the transport raises.
    project = Project()
    conn = project.conn()
    del project.core.tapir_responses["GetIFCSpatialStructure"]
    rule = ifc_rule()
    result = rule.check(build_snapshot(conn, data_needs([rule])))
    assert result.skipped is True


def test_it_reads_no_properties():
    # The property sweep is what can crash Archicad 29.
    project = Project(types={W1: "Wall"}, ifc_stories={W1: 0}, floors={W1: 0})
    conn = project.conn()
    rules = [ifc_rule(element_types=["Wall"]), parts_rule()]
    build_snapshot(conn, data_needs(rules), ifc_story_types=ifc_story_types(rules))
    assert "API.GetPropertyValuesOfElements" not in [c for c, _ in project.core.calls]


def test_the_snapshot_holds_nothing_for_rules_that_do_not_ask():
    project = Project()
    snapshot = build_snapshot(project.conn(), frozenset())
    assert snapshot.missing_parts is None and snapshot.ifc_structure is None
    assert project.core.calls == []


def test_a_structure_with_no_stories_compares_nothing():
    snapshot = ModelSnapshot(ifc_structure=IfcStructure(
        reported=frozenset({"IfcProject", "IfcSite", "IfcBuilding"}), stories={}))
    assert ifc_rule(element_types=["Wall"]).check(snapshot).passed is True


# ---------- through the tools ----------

RULES = """
- id: parts
  type: library-parts-loaded
- id: ifc-structure
  type: ifc-spatial-structure
  element_types: [Wall]
"""


async def _tool(monkeypatch, tmp_path, mode, tool, args=None, project=None):
    (tmp_path / "rules.yaml").write_text(RULES, encoding="utf-8")
    project = project or Project()
    conn = project.conn()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode=mode, rules_dir=tmp_path)) as client:
        result = await client.call_tool(tool, args or {})
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("mode", ["full", "verdicts"])
@pytest.mark.parametrize("tool, args", [("run_rule", {"rule_id": "parts"}),
                                        ("audit_delivery_readiness", {})])
async def test_the_verdict_keeps_to_ids_counts_and_guids(monkeypatch, tmp_path, mode, tool, args):
    out = await _tool(monkeypatch, tmp_path, mode, tool, args)
    assert "Old Lamp" not in json.dumps(out)
    parts = next(r for r in out["results"] if r["rule"] == "parts")
    assert parts["guids"] == [LAMP] and parts["failures"] == 1


async def test_the_ifc_readiness_tool_runs_the_structure_rule(monkeypatch, tmp_path):
    project = Project(types={W1: "Wall"}, ifc_stories={W1: 2}, floors={W1: 0})
    out = await _tool(monkeypatch, tmp_path, "full", "verify_ifc_export_readiness",
                      project=project)
    [result] = out["results"]
    assert result["rule"] == "ifc-structure" and result["guids"] == [W1]
