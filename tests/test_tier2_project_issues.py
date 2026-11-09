import json
from pathlib import PureWindowsPath

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import issues
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


# Observed twice on a live BIMcloud project: Tapir's projectLocation is a
# teamwork:// URL carrying a full JWT *refresh* token, which then lands in the
# model's context and in the session transcript.
TEAMWORK_LOCATION = (
    "teamwork://john.doe:eyJhbGciOiJIUzI1NiJ9.cmVmcmVzaA.s1gn4tur3"
    "@bimcloud.example.com:443/PROJ/250023-CVP")


def _with_project_info(monkeypatch, info):
    core = make_core()
    core.tapir_responses["GetProjectInfo"] = info
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def test_project_info_strips_teamwork_credentials(monkeypatch):
    _with_project_info(monkeypatch, {
        "projectName": "250023-CVP", "isTeamwork": True,
        "projectLocation": TEAMWORK_LOCATION, "projectPath": "PROJ/250023-CVP"})
    payload = await call("get_project_info")
    assert "eyJ" not in json.dumps(payload)  # no JWT anywhere in the output
    assert payload["project"]["projectLocation"] == \
        "teamwork://bimcloud.example.com:443/PROJ/250023-CVP"
    # The fields callers actually use survive untouched.
    assert payload["project"]["projectName"] == "250023-CVP"
    assert payload["project"]["isTeamwork"] is True


async def test_project_info_keeps_a_plain_local_path(monkeypatch):
    _with_project_info(monkeypatch, {
        "projectName": "Test House", "isTeamwork": False,
        "projectLocation": "/Users/tester/Test House.pln"})
    payload = await call("get_project_info")
    assert payload["project"]["projectLocation"] == "/Users/tester/Test House.pln"


async def test_list_attributes_layers(core):
    payload = await call("list_attributes", {"attribute_type": "Layer"})
    assert payload["names"] == ["A-WALL", "A-ZONE"]


async def test_list_attributes_unknown_type(core):
    payload = await call("list_attributes", {"attribute_type": "Pen"})
    assert "Layer" in payload["error"]


async def test_issues_list_and_create(core):
    listed = await call("list_issues")
    assert listed["issues"][0]["name"] == "Old issue"
    created = await call("create_issue", {"name": "Fix walls"})
    assert created["issue_id"] == "i-2"


async def test_issues_create_requires_name(core):
    # An empty string rather than an omitted argument: name is a required
    # parameter now, so omitting it is rejected by schema validation before the
    # tool body runs, and this is the check that the body itself still refuses.
    payload = await call("create_issue", {"name": ""})
    assert "name" in payload["error"]
    assert not any(cmd == "CreateIssue" for cmd, _ in core.calls)


async def test_publish(core):
    payload = await call("publish", {"publisher_set_name": "IFC Export"})
    assert payload == {"published": "IFC Export"}
    command, params = [c for c in core.calls if c[0] == "PublishPublisherSet"][0]
    assert params == {"publisherSetName": "IFC Export"}


def _with_sets(core, *names):
    core.tapir_responses["GetPublisherSets"] = {"publisherSets": [
        {"name": n, "rootNavigatorItemId": {"guid": f"nav-{i}"}} for i, n in enumerate(names)]}


async def test_publish_runs_a_set_the_project_has(core):
    _with_sets(core, "PDF Set", "IFC Export")
    payload = await call("publish", {"publisher_set_name": "IFC Export"})
    assert payload == {"published": "IFC Export"}


async def test_publish_refuses_a_name_the_project_does_not_have_and_lists_the_sets(core):
    _with_sets(core, "PDF Set", "IFC Export")
    payload = await call("publish", {"publisher_set_name": "IFC Exprt"})
    assert "IFC Exprt" in payload["error"]
    assert payload["publisher_sets"] == ["PDF Set", "IFC Export"]
    assert not any(cmd == "PublishPublisherSet" for cmd, _ in core.calls)


async def test_publish_lets_archicad_judge_when_the_sets_cannot_be_read(core):
    # No canned GetPublisherSets: it fails the way the transport does.
    payload = await call("publish", {"publisher_set_name": "IFC Export"})
    assert payload == {"published": "IFC Export"}


class _WindowsPath(PureWindowsPath):
    """What Path is on Windows, on any machine, for how it prints separators."""

    def expanduser(self):
        return self


class _RecordingConnection:
    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append((command, params))
        return {}


@pytest.mark.parametrize("call, command, key", [
    (issues.export_issues_bcf, "ExportIssuesToBCF", "exportPath"),
    (issues.import_issues_bcf, "ImportIssuesFromBCF", "importPath"),
])
def test_a_bcf_path_reaches_archicad_with_native_separators(monkeypatch, call, command, key):
    monkeypatch.setattr(issues, "Path", _WindowsPath, raising=False)
    conn = _RecordingConnection()
    out = call(conn, "C:/Projects/Model/issues.bcf")
    assert [c for c, _ in conn.calls] == [command]
    assert conn.calls[0][1][key] == r"C:\Projects\Model\issues.bcf"
    assert r"C:\Projects\Model\issues.bcf" in out.values()
