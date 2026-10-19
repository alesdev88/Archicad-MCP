"""No tool hands a Teamwork credential to the model.

On a Teamwork project Tapir's GetProjectInfo.projectLocation is
teamwork://<user email>:<BIMcloud refresh token>@<host>/<path>. get_project_info
always stripped it, but execute_read_api_command("GetProjectInfo") returned it
verbatim (seen live on a BIMcloud project on 19.10.2026), and a script could
return or print it. Every location and token here is fake.
"""
import json

import pytest
from fastmcp import Client
from multiconn_archicad.errors import TapirCommandError

import archicad_mcp.scripting.tools as tools_mod
import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.core.project import scrub_credentials
from archicad_mcp.gateway.execute import execute_read_api_command
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.api import Recorder, ScriptAPI
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.cmVmcmVzaA.s1gn4tur3"
TEAMWORK_LOCATION = (f"teamwork://john.doe@example.com:{FAKE_TOKEN}"
                     "@bimcloud.example.com:443/PROJ/250023-CVP")
SCRUBBED_LOCATION = "teamwork://bimcloud.example.com:443/PROJ/250023-CVP"
TEAMWORK_INFO = {"projectName": "250023-CVP", "isUntitled": False,
                 "isTeamwork": True, "projectLocation": TEAMWORK_LOCATION,
                 "projectPath": "PROJ/250023-CVP"}


def assert_clean(payload):
    text = json.dumps(payload)
    assert "eyJ" not in text
    assert "s1gn4tur3" not in text
    assert "john.doe" not in text


# ---------- the scrub itself ----------

def test_a_teamwork_url_loses_its_user_and_token():
    assert scrub_credentials(TEAMWORK_LOCATION) == SCRUBBED_LOCATION


def test_every_string_in_a_nested_answer_is_scrubbed():
    answer = {"hotlinks": [{"location": TEAMWORK_LOCATION, "children": []}],
              TEAMWORK_LOCATION: 1, "count": 3, "ok": True, "none": None}
    assert scrub_credentials(answer) == {
        "hotlinks": [{"location": SCRUBBED_LOCATION, "children": []}],
        SCRUBBED_LOCATION: 1, "count": 3, "ok": True, "none": None}


def test_a_url_inside_free_text_is_scrubbed():
    # How the location reaches a traceback or a script's print output.
    text = f"KeyError: {{'projectLocation': '{TEAMWORK_LOCATION}'}}\n"
    assert scrub_credentials(text) == (
        f"KeyError: {{'projectLocation': '{SCRUBBED_LOCATION}'}}\n")


def test_a_bare_token_is_redacted():
    assert scrub_credentials(f"refresh {FAKE_TOKEN} here") == \
        "refresh [redacted-token] here"


@pytest.mark.parametrize("text", [
    "/Users/tester/Test House.pln", "PROJ/250023-CVP", "EI30",
    "mail john@example.com", "https://example.com/a?b=c@d",
    "https://example.com/path@v2", ""])
def test_ordinary_text_is_left_alone(text):
    assert scrub_credentials(text) == text


# ---------- the gateway ----------

@pytest.fixture
def core(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["GetProjectInfo"] = dict(TEAMWORK_INFO)
    tapir["GetStories"] = {"stories": []}
    tapir["GetGeoLocation"] = {}
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None, **server_kwargs):
    async with Client(build_server(mode="full", **server_kwargs)) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_read_gateway_scrubs_project_info(core):
    payload = await call("execute_read_api_command", {"name": "GetProjectInfo"})
    assert_clean(payload)
    assert payload["projectLocation"] == SCRUBBED_LOCATION
    # What callers actually use survives.
    assert payload["projectName"] == "250023-CVP"
    assert payload["isTeamwork"] is True


async def test_read_gateway_scrubs_any_answer(core):
    core.tapir_responses["GetHotlinks"] = {"hotlinks": [
        {"location": TEAMWORK_LOCATION, "children": []}]}
    payload = await call("execute_read_api_command", {"name": "GetHotlinks"})
    assert_clean(payload)
    assert payload["hotlinks"][0]["location"] == SCRUBBED_LOCATION


async def test_write_gateway_scrubs_its_answer(core):
    core.tapir_responses["HighlightElements"] = {"note": TEAMWORK_LOCATION}
    payload = await call("execute_write_api_command", {
        "name": "HighlightElements",
        "params": {"elements": [{"elementId": {
            "guid": "00000000-0000-0000-0000-000000000001"}}],
            "highlightedColors": [[255, 0, 0, 255]]},
        "confirm": True})
    assert_clean(payload)
    assert payload["note"] == SCRUBBED_LOCATION


def test_the_gateway_scrubs_before_any_tool_wrapper():
    # The gateway functions are also what scripts reach, so the scrub cannot
    # live only in the MCP layer.
    core = FakeCore(official=dict(api_replays.OFFICIAL),
                    tapir={"GetProjectInfo": dict(TEAMWORK_INFO)})
    out = execute_read_api_command(build_registry(),
                                   lambda: ArchicadConnection(19723, core=core),
                                   "GetProjectInfo")
    assert_clean(out)
    assert out["projectLocation"] == SCRUBBED_LOCATION


def test_a_script_reading_project_info_never_sees_the_token():
    core = FakeCore(official=dict(api_replays.OFFICIAL),
                    tapir={"GetProjectInfo": dict(TEAMWORK_INFO)})
    ac = ScriptAPI(ArchicadConnection(19723, core=core), build_registry(), Recorder())
    info = ac.cmd("GetProjectInfo")
    assert_clean(info)
    assert info["projectLocation"] == SCRUBBED_LOCATION


# ---------- every tool's reply ----------

async def test_an_api_error_carrying_the_location_is_scrubbed(core):
    def refuse(params):
        raise TapirCommandError(message=f"cannot open {TEAMWORK_LOCATION}", code=None)
    core.tapir_responses["GetHotlinks"] = refuse
    payload = await call("execute_read_api_command", {"name": "GetHotlinks"})
    assert_clean(payload)
    assert SCRUBBED_LOCATION in payload["error"]


async def test_get_project_info_scrubs_its_hotlinks_too(core):
    core.tapir_responses["GetHotlinks"] = {"hotlinks": [
        {"location": TEAMWORK_LOCATION, "children": []}]}
    payload = await call("get_project_info")
    assert_clean(payload)
    assert payload["hotlinks"][0]["location"] == SCRUBBED_LOCATION


INFO = InstanceInfo(port=19723, version=29, build=5101, project_name="250023-CVP",
                    tapir_available=True, tapir_version="1.7.1")


@pytest.fixture
def scripts(monkeypatch, core):
    monkeypatch.setattr(tools_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    monkeypatch.setattr(tools_mod, "probe_port", lambda port: INFO)

    def reply_with(reply):
        monkeypatch.setattr(tools_mod.child, "run_child",
                            lambda code, port, timeout_s: reply)
    return reply_with


async def test_run_script_output_is_scrubbed(scripts):
    # A script that calls Tapir through the connection directly bypasses the
    # gateway, so what it returns, prints or raises is scrubbed on the way out.
    scripts({"result": {"info": dict(TEAMWORK_INFO)},
             "stdout": f"{dict(TEAMWORK_INFO)!r}\n",
             "error": f"ValueError: {TEAMWORK_LOCATION}",
             "traceback": f"Traceback...\nValueError: {TEAMWORK_LOCATION}",
             "operations": [], "skipped": []})
    payload = await call("run_script", {"code": "irrelevant"}, enable_scripts=True)
    assert_clean(payload)
    assert payload["result"]["info"]["projectLocation"] == SCRUBBED_LOCATION
    assert SCRUBBED_LOCATION in payload["stdout"]


async def test_capped_run_script_output_is_scrubbed(scripts):
    # A large result comes back as capped JSON text, so the scrub has to work
    # on text as well as on fields.
    scripts({"result": [dict(TEAMWORK_INFO)] * 50, "stdout": "",
             "operations": [], "skipped": []})
    payload = await call("run_script", {"code": "irrelevant",
                                        "max_output_chars": 2000},
                         enable_scripts=True)
    assert payload["truncated"] == ["result"]
    assert_clean(payload)


async def test_apply_changeset_scrubs_refusals(scripts, monkeypatch):
    scripts({"result": None, "stdout": "", "error": None, "traceback": None,
             "operations": [{"kind": "command", "name": "SetGDLParametersOfElements",
                             "params": {}}],
             "skipped": []})
    apply_core = FakeCore(official=dict(api_replays.OFFICIAL), tapir={
        **api_replays.TAPIR,
        "GetProjectInfo": dict(TEAMWORK_INFO),
        "SetGDLParametersOfElements": {"executionResults": [
            {"success": False,
             "error": {"code": 1, "message": f"locked in {TEAMWORK_LOCATION}"}}]}})
    monkeypatch.setattr(tools_mod, "open_connection",
                        lambda port: ArchicadConnection(port, core=apply_core))
    async with Client(build_server(mode="full", enable_scripts=True)) as client:
        planned = json.loads((await client.call_tool(
            "run_script", {"code": "irrelevant"})).content[0].text)
        done = json.loads((await client.call_tool("apply_changeset", {
            "changeset_id": planned["changeset"]["id"],
            "confirm": True})).content[0].text)
    assert done["commands"][0]["failed"] == 1
    assert_clean(done)
