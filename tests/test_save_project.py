"""save_project: SaveProject behind a confirm, with the failure explained."""
import json
import os

import pytest
from fastmcp import Client
from multiconn_archicad.errors import TapirCommandError

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

TEAMWORK_LOCATION = ("teamwork://u:eyJhbGciOiJIUzI1NiJ9.cmVmcmVzaA.s1g"
                     "@bimcloud.example.com:443/PROJ/P")


def _core(info: dict, save=None, missing=frozenset()):
    official = dict(api_replays.OFFICIAL)
    official["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] not in missing}
    tapir = dict(api_replays.TAPIR)
    tapir["GetProjectInfo"] = info
    tapir["SaveProject"] = save if save is not None else {"success": True}
    return FakeCore(official=official, tapir=tapir)


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / "House.pln"
    path.write_bytes(b"pln")
    os.utime(path, (1_000_000, 1_000_000))
    return path


def _local(plan) -> dict:
    return {"isUntitled": False, "isTeamwork": False, "projectName": "House",
            "projectLocation": str(plan), "projectPath": str(plan)}


def _use(monkeypatch, core):
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))


async def call(args=None, mode="full"):
    async with Client(build_server(mode=mode)) as client:
        result = await client.call_tool("save_project", args or {})
        return json.loads(result.content[0].text)


def _saved(core) -> bool:
    return any(c == "SaveProject" for c, _ in core.calls)


async def test_dry_run_sends_nothing(monkeypatch, plan):
    core = _core(_local(plan))
    _use(monkeypatch, core)
    out = await call()
    assert out["dry_run"] is True
    assert out["project"] == "House"
    assert out["location"] == str(plan)
    assert out["is_teamwork"] is False
    assert not _saved(core)


async def test_untitled_is_refused_before_sending(monkeypatch):
    core = _core({"isUntitled": True, "isTeamwork": False})
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert "File > Save As" in out["error"]
    assert not _saved(core)


async def test_save_reports_file_updated(monkeypatch, plan):
    def save(params):
        os.utime(plan, (2_000_000, 2_000_000))
        return {"success": True}
    core = _core(_local(plan), save=save)
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert out["saved"] is True
    assert out["file_updated"] is True
    assert _saved(core)


async def test_save_that_left_the_file_reports_it(monkeypatch, plan):
    core = _core(_local(plan))
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert out["saved"] is True
    assert out["file_updated"] is False
    assert out["note"]


async def test_failure_names_the_older_version_case(monkeypatch, plan):
    def save(params):
        raise TapirCommandError(message="Failed to save the project.", code=-2130313112)
    core = _core(_local(plan), save=save)
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert "Failed to save the project." in out["error"]
    assert "-2130313112" in out["error"]
    assert "older Archicad" in out["error"]
    assert "Save As" in out["error"]
    assert "not writable" not in out["error"]


async def test_failure_says_when_the_folder_is_not_writable(monkeypatch, plan):
    def save(params):
        raise TapirCommandError(message="Failed to save the project.", code=-1)
    core = _core(_local(plan), save=save)
    _use(monkeypatch, core)
    monkeypatch.setattr("archicad_mcp.core.save.os.access", lambda path, mode: False)
    out = await call({"confirm": True})
    assert "not writable" in out["error"]


async def test_teamwork_saves_locally_and_sends_nothing(monkeypatch):
    core = _core({"isUntitled": False, "isTeamwork": True, "projectName": "P",
                  "projectLocation": TEAMWORK_LOCATION})
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert out["saved"] is True
    assert out["sent"] is False
    assert "Send" in out["note"]
    assert "file_updated" not in out
    assert "eyJ" not in json.dumps(out)


async def test_teamwork_dry_run_says_it_will_not_send(monkeypatch):
    core = _core({"isUntitled": False, "isTeamwork": True, "projectName": "P",
                  "projectLocation": TEAMWORK_LOCATION})
    _use(monkeypatch, core)
    out = await call()
    assert out["is_teamwork"] is True
    assert "Send" in out["note"]
    assert "eyJ" not in json.dumps(out)


async def test_verdicts_mode_hides_identity(monkeypatch, plan):
    core = _core(_local(plan))
    _use(monkeypatch, core)
    out = await call(mode="verdicts")
    assert out["dry_run"] is True
    assert "project" not in out
    assert "location" not in out
    assert str(plan) not in json.dumps(out)


async def test_needs_save_project_command(monkeypatch, plan):
    core = _core(_local(plan), missing={"SaveProject"})
    _use(monkeypatch, core)
    out = await call({"confirm": True})
    assert "1.3.1" in out["error"]
    assert not _saved(core)
