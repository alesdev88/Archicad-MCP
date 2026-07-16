import pytest

from archicad_mcp.connection import (
    ArchicadConnection,
    ArchicadUnavailableError,
    get_connection,
    probe_port,
)
from tests.conftest import FakeCore

PRODUCT_INFO = {"version": 29, "buildNumber": 5003, "languageCode": "INT"}
TAPIR_ON = {"API.GetProductInfo": PRODUCT_INFO,
            "API.IsAddOnCommandAvailable": {"available": True}}
TAPIR_OFF = {"API.GetProductInfo": PRODUCT_INFO,
             "API.IsAddOnCommandAvailable": {"available": False}}


def test_probe_port_with_tapir():
    core = FakeCore(official=TAPIR_ON,
                    tapir={"GetProjectInfo": {"projectName": "Test House", "untitled": False,
                                              "teamwork": False},
                           "GetAddOnVersion": {"version": "1.8.2"}})
    info = probe_port(19723, core=core)
    assert info is not None
    assert info.version == 29 and info.tapir_available is True
    assert info.project_name == "Test House"
    assert info.tapir_version == "1.8.2"


def test_probe_port_without_tapir_still_reports_instance():
    info = probe_port(19723, core=FakeCore(official=TAPIR_OFF))
    assert info is not None
    assert info.tapir_available is False and info.project_name is None


def test_probe_port_no_listener_returns_none(monkeypatch):
    from multiconn_archicad.errors import APIConnectionError

    class DeadCore:
        def post_command(self, *a, **k):
            raise APIConnectionError(message="connection refused", code=None)

    assert probe_port(19723, core=DeadCore()) is None


def test_connection_official_and_tapir_roundtrip():
    core = FakeCore(official=TAPIR_ON, tapir={"GetStories": {"stories": []}})
    conn = ArchicadConnection(19723, core=core)
    assert conn.official("API.GetProductInfo")["version"] == 29
    assert conn.tapir("GetStories") == {"stories": []}


def test_tapir_call_without_addon_gives_actionable_error():
    conn = ArchicadConnection(19723, core=FakeCore(official=TAPIR_OFF))
    with pytest.raises(ArchicadUnavailableError, match="Tapir add-on"):
        conn.tapir("GetStories")


def test_get_connection_no_instances(monkeypatch):
    monkeypatch.setattr("archicad_mcp.connection.discover_instances", lambda: [])
    with pytest.raises(ArchicadUnavailableError, match="Start Archicad"):
        get_connection(None)


def test_get_connection_multiple_instances(monkeypatch):
    from archicad_mcp.connection import InstanceInfo
    two = [InstanceInfo(19723, 29, 1, None, False, None),
           InstanceInfo(19724, 29, 1, None, False, None)]
    monkeypatch.setattr("archicad_mcp.connection.discover_instances", lambda: two)
    with pytest.raises(ArchicadUnavailableError, match="19723"):
        get_connection(None)
