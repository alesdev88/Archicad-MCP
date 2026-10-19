"""export_ifc: IFCFileOperation save with a named translator, dry run first."""
import json
import os
from pathlib import Path

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

TRANSLATORS = [{"name": "General Parametric Export", "preview": True},
               {"name": "IFC4 Reference View-based Export", "preview": False}]


class Archicad:
    def __init__(self, window="FloorPlan", selected=(), writes=True, missing=()):
        self.window = window
        self.selected = list(selected)
        self.writes = writes
        self.missing = set(missing)

    def save(self, params):
        if self.writes:
            Path(params["ifcFilePath"]).write_bytes(b"ISO-10303-21;")
        return {"success": True}

    def change_window(self, params):
        self.window = params["windowType"]
        return {"success": True}

    def core(self) -> FakeCore:
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing}
        tapir = dict(api_replays.TAPIR)
        tapir.update({
            "GetIFCExportTranslators": {"translators": TRANSLATORS},
            "IFCFileOperation": self.save,
            "ChangeWindow": self.change_window,
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
            "GetSelectedElements": lambda p: {"elements": [
                {"elementId": {"guid": g}} for g in self.selected]},
            "GetStories": {"firstStory": 0, "lastStory": 1, "actStory": 1,
                           "skipNullFloor": False,
                           "stories": [{"index": 0, "name": "Ground"},
                                       {"index": 1, "name": "First"}]},
        })
        return FakeCore(official=official, tapir=tapir)


@pytest.fixture
def archicad(monkeypatch):
    def use(**kwargs):
        fake = Archicad(**kwargs)
        core = fake.core()
        monkeypatch.setattr(server_mod, "get_connection",
                            lambda port: ArchicadConnection(19723, core=core))
        return fake, core
    return use


async def export(args):
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("export_ifc", args)
        return json.loads(result.content[0].text)


def saves(core) -> list[dict]:
    return [p for c, p in core.calls if c == "IFCFileOperation"]


async def test_dry_run_shows_translator_path_and_scope(archicad, tmp_path):
    _, core = archicad()
    target = tmp_path / "house.ifc"
    out = await export({"path": str(target)})
    assert out["dry_run"] is True
    assert out["path"] == str(target)
    assert out["file_type"] == "ifc"
    assert out["translator"] == "General Parametric Export"
    assert out["translator_is_default"] is True
    assert out["translators"] == [t["name"] for t in TRANSLATORS]
    assert out["scope"] == "VisibleElementsOnAllStories"
    assert out["active_window"] == "FloorPlan"
    assert out["exists"] is False
    assert saves(core) == [] and not target.exists()


async def test_translator_matches_ignoring_case(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "a.ifc"),
                        "translator": "ifc4 reference view-based export"})
    assert out["translator"] == "IFC4 Reference View-based Export"
    assert out["translator_is_default"] is False


async def test_unknown_translator_lists_the_names(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "a.ifc"), "translator": "Coordination"})
    assert "Coordination" in out["error"]
    for t in TRANSLATORS:
        assert t["name"] in out["error"]


async def test_scope_is_validated_and_matched_ignoring_case(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "a.ifc"), "scope": "entireproject"})
    assert out["scope"] == "EntireProject"
    out = await export({"path": str(tmp_path / "a.ifc"), "scope": "Everything"})
    assert "EntireProject" in out["error"] and "SelectedElementsOnly" in out["error"]


async def test_relative_path_is_refused(archicad):
    archicad()
    out = await export({"path": "exports/a.ifc"})
    assert "absolute" in out["error"]


async def test_missing_folder_is_refused(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "nope" / "a.ifc")})
    assert "folder" in out["error"] and "nope" in out["error"]


async def test_a_folder_is_refused(archicad, tmp_path):
    archicad()
    (tmp_path / "dir.ifc").mkdir()
    out = await export({"path": str(tmp_path / "dir.ifc"), "overwrite": True})
    assert "folder" in out["error"]


async def test_tilde_is_expanded(archicad, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    archicad()
    out = await export({"path": "~/a.ifc", "dry_run": False})
    assert out["exported"] == str(tmp_path / "a.ifc")
    assert (tmp_path / "a.ifc").is_file()


async def test_upper_case_extension_is_ifc(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "A.IFC")})
    assert out["file_type"] == "ifc"


async def test_ifczip_extension(archicad, tmp_path):
    _, core = archicad()
    out = await export({"path": str(tmp_path / "a.ifczip"), "dry_run": False})
    assert out["file_type"] == "ifczip"
    assert saves(core)[0]["fileType"] == "ifczip"


async def test_other_extension_is_refused(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "a.ifcxml")})
    assert ".ifc" in out["error"] and ".ifczip" in out["error"]


async def test_existing_file_is_refused_without_overwrite(archicad, tmp_path):
    _, core = archicad()
    target = tmp_path / "a.ifc"
    target.write_bytes(b"old")
    for dry_run in (True, False):
        out = await export({"path": str(target), "dry_run": dry_run})
        assert "overwrite=true" in out["error"]
    assert saves(core) == [] and target.read_bytes() == b"old"


async def test_overwrite_replaces_it(archicad, tmp_path):
    archicad()
    target = tmp_path / "a.ifc"
    target.write_bytes(b"old")
    os.utime(target, (1_000_000, 1_000_000))
    out = await export({"path": str(target), "overwrite": True})
    assert out["exists"] is True and out["dry_run"] is True
    out = await export({"path": str(target), "overwrite": True, "dry_run": False})
    assert out["exported"] == str(target)
    assert target.read_bytes() == b"ISO-10303-21;"


async def test_commit_sends_the_save_and_reports_the_file(archicad, tmp_path):
    _, core = archicad()
    target = tmp_path / "a.ifc"
    out = await export({"path": str(target), "dry_run": False})
    assert saves(core) == [{"method": "save", "ifcFilePath": str(target), "fileType": "ifc",
                            "translatorName": "General Parametric Export",
                            "elementsToExport": "VisibleElementsOnAllStories"}]
    assert out["exported"] == str(target)
    assert out["bytes"] == len(b"ISO-10303-21;")
    assert out["seconds"] >= 0
    assert out["translator"] == "General Parametric Export"
    assert out["scope"] == "VisibleElementsOnAllStories"
    assert "switched_window" not in out


async def test_success_without_a_file_is_an_error(archicad, tmp_path):
    archicad(writes=False)
    out = await export({"path": str(tmp_path / "a.ifc"), "dry_run": False})
    assert "no file" in out["error"]


async def test_overwrite_success_that_left_the_old_file_is_an_error(archicad, tmp_path):
    archicad(writes=False)
    target = tmp_path / "a.ifc"
    target.write_bytes(b"old")
    out = await export({"path": str(target), "overwrite": True, "dry_run": False})
    assert "not rewritten" in out["error"]


async def test_section_window_switches_to_the_floor_plan(archicad, tmp_path):
    fake, core = archicad(window="Section")
    out = await export({"path": str(tmp_path / "a.ifc")})
    assert out["active_window"] == "Section"
    assert "floor plan" in out["note"]
    assert not any(c == "ChangeWindow" for c, _ in core.calls)
    out = await export({"path": str(tmp_path / "a.ifc"), "dry_run": False})
    names = [c for c, _ in core.calls if c in ("ChangeWindow", "IFCFileOperation")]
    assert names == ["ChangeWindow", "IFCFileOperation"]
    assert [p for c, p in core.calls if c == "ChangeWindow"] == [{"windowType": "FloorPlan"}]
    assert out["switched_window"] == {"from": "Section", "to": "FloorPlan"}


async def test_3d_window_exports_without_switching(archicad, tmp_path):
    _, core = archicad(window="3DModel")
    out = await export({"path": str(tmp_path / "a.ifc"), "dry_run": False})
    assert out["exported"]
    assert not any(c == "ChangeWindow" for c, _ in core.calls)


async def test_selected_only_needs_a_selection(archicad, tmp_path):
    archicad(selected=[])
    out = await export({"path": str(tmp_path / "a.ifc"), "scope": "SelectedElementsOnly"})
    assert "nothing is selected" in out["error"]


async def test_selected_only_counts_the_selection(archicad, tmp_path):
    archicad(selected=["A", "B"])
    out = await export({"path": str(tmp_path / "a.ifc"), "scope": "SelectedElementsOnly"})
    assert out["selected"] == 2


async def test_selected_only_refused_from_another_window(archicad, tmp_path):
    _, core = archicad(window="Layout", selected=["A"])
    out = await export({"path": str(tmp_path / "a.ifc"), "scope": "SelectedElementsOnly",
                        "dry_run": False})
    assert "floor plan or the 3D window" in out["error"]
    assert saves(core) == []


async def test_current_story_scope_names_the_story(archicad, tmp_path):
    archicad()
    out = await export({"path": str(tmp_path / "a.ifc"),
                        "scope": "VisibleElementsOnCurrentStory"})
    assert out["story"] == {"index": 1, "name": "First"}


async def test_needs_tapir_1_6_0(archicad, tmp_path):
    _, core = archicad(missing={"GetIFCExportTranslators"})
    out = await export({"path": str(tmp_path / "a.ifc")})
    assert "1.6.0" in out["error"]
    assert saves(core) == []


async def test_export_ifc_is_full_mode_only():
    async with Client(build_server(mode="verdicts")) as client:
        names = {t.name for t in await client.list_tools()}
    assert "export_ifc" not in names
