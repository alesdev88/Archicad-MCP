import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_core(tapir_on=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir_on:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    tapir = dict(api_replays.TAPIR)
    tapir.update({
        "GetStories": {"stories": [{"index": 0, "name": "Ground"},
                                   {"index": 1, "name": "First"}]},
        "GetHotlinks": {"hotlinks": []},
        "GetGeoLocation": {"projectLocation": {"longitude": 14.5, "latitude": 46.05}},
        "GetIssues": {"issues": [{"issueId": {"guid": "i-1"}, "name": "Old issue"}]},
        "CreateIssue": {"issueId": {"guid": "i-2"}},
        "AddCommentToIssue": {},
        "AttachElementsToIssue": {},
        "ExportIssuesToBCF": {},
        "PublishPublisherSet": {},
    })
    return FakeCore(official=official, tapir=tapir if tapir_on else {})


@pytest.fixture
def core(monkeypatch):
    core = make_core()
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_project_info_with_tapir(core):
    payload = await call("get_project_info")
    assert payload["archicad_version"] == 29
    assert payload["project"]["projectName"] == "Test House"
    assert len(payload["stories"]) == 2
    assert payload["geolocation_present"] is True


async def test_project_info_without_tapir(monkeypatch):
    core = make_core(tapir_on=False)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    payload = await call("get_project_info")
    assert payload["archicad_version"] == 29
    assert "Tapir" in payload["note"]


async def test_list_attributes_layers(core):
    payload = await call("list_attributes", {"attribute_type": "Layer"})
    assert payload["names"] == ["A-WALL", "A-ZONE"]


async def test_list_attributes_unknown_type(core):
    payload = await call("list_attributes", {"attribute_type": "Pen"})
    assert "Layer" in payload["error"]


async def test_issues_list_and_create(core):
    listed = await call("manage_issues", {"action": "list"})
    assert listed["issues"][0]["name"] == "Old issue"
    created = await call("manage_issues", {"action": "create", "name": "Fix walls"})
    assert created["issue_id"] == "i-2"


async def test_issues_create_requires_name(core):
    payload = await call("manage_issues", {"action": "create"})
    assert "name" in payload["error"]


async def test_publish(core):
    payload = await call("publish", {"publisher_set_name": "IFC Export"})
    assert payload == {"published": "IFC Export"}
    command, params = [c for c in core.calls if c[0] == "PublishPublisherSet"][0]
    assert params == {"publisherSetName": "IFC Export"}
