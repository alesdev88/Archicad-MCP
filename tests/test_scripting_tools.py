"""run_script and apply_changeset as MCP tools, and the switch that registers them."""
import json

import pytest
from fastmcp import Client

import archicad_mcp.scripting.tools as tools_mod
from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.server import (
    build_server,
    check_script_transport,
    format_startup_banner,
    resolve_flag,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays


async def _names(**kwargs):
    async with Client(build_server(**kwargs)) as client:
        return {t.name for t in await client.list_tools()}


async def test_scripts_are_off_by_default():
    names = await _names(mode="full")
    assert "run_script" not in names and "apply_changeset" not in names


async def test_scripts_register_in_full_mode_when_enabled():
    names = await _names(mode="full", enable_scripts=True)
    assert {"run_script", "apply_changeset"} <= names


async def test_verdicts_mode_ignores_the_switch():
    assert "run_script" not in await _names(mode="verdicts", enable_scripts=True)


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("no", False),
    ("1", True), ("true", True), ("TRUE", True), (" yes ", True)])
def test_resolve_flag(raw, expected):
    assert resolve_flag(raw) is expected


def test_http_needs_the_second_flag():
    message = check_script_transport(True, "full", "http", False)
    assert "--allow-scripts-over-http" in message
    assert check_script_transport(True, "full", "http", True) is None
    assert check_script_transport(True, "full", "stdio", False) is None
    assert check_script_transport(False, "full", "http", False) is None
    # Verdicts mode never registers the tools, so there is nothing to refuse.
    assert check_script_transport(True, "verdicts", "http", False) is None


def test_banner_names_the_script_state():
    assert "scripts off" in format_startup_banner("full", 1, [])
    assert "scripts on" in format_startup_banner("full", 1, [], scripts_enabled=True)
    assert "scripts ignored in verdicts mode" in format_startup_banner(
        "verdicts", 1, [], scripts_enabled=True)


# ---------- execute_script, the logic behind run_script ----------

INFO = InstanceInfo(port=19723, version=29, build=5101, project_name="Test House",
                    tapir_available=True, tapir_version="1.5.9")


@pytest.fixture
def wired(monkeypatch):
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(tools_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    monkeypatch.setattr(tools_mod, "probe_port", lambda port: INFO)
    calls = []

    def fake_run(reply):
        def run(code, port, timeout_s):
            calls.append((code, port, timeout_s))
            return reply
        monkeypatch.setattr(tools_mod.child, "run_child", run)
    return fake_run, calls


def _write(guid):
    return {"guid": guid, "property": "OFFICE/Fire Rating", "current": None,
            "new": "EI30", "payload": {"elementId": {"guid": guid}}}


def test_a_plan_becomes_a_changeset(wired):
    fake_run, calls = wired
    fake_run({"result": {"n": 1}, "stdout": "", "error": None, "traceback": None,
              "operations": [{"kind": "props", "writes": [_write("w-1")]}],
              "skipped": []})
    store = tools_mod.ChangesetStore()
    out = tools_mod.execute_script(store, "code", None, 120, 20000)
    assert out["result"] == {"n": 1}
    assert out["changeset"]["property_writes"] == 1
    assert out["changeset"]["project"] == "Test House"
    assert store.lookup(out["changeset"]["id"]).port == 19723
    assert calls == [("code", 19723, 120.0)]


def test_a_script_with_no_writes_offers_no_changeset(wired):
    fake_run, _ = wired
    fake_run({"result": 1, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert "changeset" not in out


def test_skipped_changes_are_shown_even_with_nothing_to_apply(wired):
    fake_run, _ = wired
    skipped = [{"guid": "d-1", "property": "D/P", "reason": "cannot hold '001'"}]
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": skipped})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert "changeset" not in out
    assert out["skipped"] == 1 and out["skipped_sample"] == skipped


def test_an_error_is_passed_through_without_a_changeset(wired):
    fake_run, _ = wired
    fake_run({"result": None, "stdout": "partial", "error": "KeyError: 'x'",
              "traceback": "Traceback...", "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert out["error"] == "KeyError: 'x'"
    assert out["traceback"] == "Traceback..."
    assert out["stdout"] == "partial"
    assert "changeset" not in out


def test_outputs_are_capped_and_marked(wired):
    fake_run, _ = wired
    fake_run({"result": list(range(1000)), "stdout": "y" * 100, "error": None,
              "traceback": None, "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 50)
    assert out["truncated"] == ["result", "stdout"]
    assert "truncated" in out["result"] and "truncated" in out["stdout"]


def test_the_timeout_is_clamped(wired):
    fake_run, calls = wired
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": []})
    tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 5000, 20000)
    tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 0, 20000)
    assert [c[2] for c in calls] == [600.0, 1.0]


# ---------- end to end through the MCP layer ----------

async def test_run_then_apply_through_the_tools(wired, monkeypatch):
    fake_run, _ = wired
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [{"kind": "props", "writes": [_write("w-1")]}],
              "skipped": []})
    written = []
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = lambda p: (
        written.extend(p["elementPropertyValues"])
        or {"executionResults": [{"success": True}]})
    apply_core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(tools_mod, "open_connection",
                        lambda port: ArchicadConnection(port, core=apply_core))

    mcp = build_server(mode="full", enable_scripts=True)
    async with Client(mcp) as client:
        planned = json.loads((await client.call_tool(
            "run_script", {"code": "irrelevant"})).content[0].text)
        cs_id = planned["changeset"]["id"]
        refused = json.loads((await client.call_tool(
            "apply_changeset", {"changeset_id": cs_id})).content[0].text)
        assert "confirm=true" in refused["error"]
        assert written == []
        done = json.loads((await client.call_tool(
            "apply_changeset", {"changeset_id": cs_id, "confirm": True})).content[0].text)
    assert done["applied"] == 1
    assert written == [{"elementId": {"guid": "w-1"}}]


def test_the_changeset_records_the_project_identity(wired):
    fake_run, _ = wired
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [{"kind": "props", "writes": [_write("w-1")]}],
              "skipped": []})
    store = tools_mod.ChangesetStore()
    out = tools_mod.execute_script(store, "code", None, 120, 20000)
    assert out["changeset"]["teamwork"] is False
    assert store.lookup(out["changeset"]["id"]).identity == {
        "name": "Test House", "is_teamwork": False,
        "location": "/Users/tester/Test House.pln"}
