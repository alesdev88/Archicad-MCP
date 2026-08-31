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


def test_dead_archicad_is_not_reported_as_missing_tapir():
    """A transport failure means Archicad is gone, not that Tapir is absent."""
    from multiconn_archicad.errors import APIConnectionError

    class DeadCore:
        def post_command(self, *a, **k):
            raise APIConnectionError(message="Server disconnected", code=None)

    conn = ArchicadConnection(19723, core=DeadCore())
    with pytest.raises(ArchicadUnavailableError, match="not responding"):
        conn.tapir_available()
    with pytest.raises(ArchicadUnavailableError, match="not responding"):
        conn.tapir("GetStories")


def test_instance_with_no_open_project_is_reported_not_hidden():
    """Live-verified: Archicad refuses even GetProductInfo (code 4001) when no
    project is open. It must still be listed, with project_open=False."""
    from multiconn_archicad.errors import StandardAPIError

    class NoProjectCore:
        def post_command(self, *a, **k):
            raise StandardAPIError(message="Invalid program status (no open project)",
                                   code=4001)

    info = probe_port(19723, core=NoProjectCore())
    assert info is not None, "a project-less instance must still be discovered"
    assert info.project_open is False
    assert info.port == 19723
    # The raw refusal is kept: it is the only record of WHY the probe failed.
    assert info.status_error == "code 4001: Invalid program status (no open project)"


def test_discovery_survives_a_project_less_instance(monkeypatch):
    """One project-less instance must not break discovery of the healthy ones."""
    from archicad_mcp.connection import InstanceInfo, discover_instances

    def fake_probe(port, core=None):
        if port == 19723:
            return InstanceInfo(19723, 0, 0, None, False, None, project_open=False)
        if port == 19724:
            return InstanceInfo(19724, 29, 4006, "Test", True, "1.5.3")
        return None

    monkeypatch.setattr("archicad_mcp.connection.probe_port", fake_probe)
    found = discover_instances()
    assert [i.port for i in found] == [19723, 19724]


def test_get_connection_prefers_the_instance_with_a_project(monkeypatch):
    from archicad_mcp.connection import InstanceInfo, get_connection
    two = [InstanceInfo(19723, 0, 0, None, False, None, project_open=False),
           InstanceInfo(19724, 29, 4006, "Test", True, "1.5.3")]
    monkeypatch.setattr("archicad_mcp.connection.discover_instances", lambda: two)
    monkeypatch.setattr("archicad_mcp.connection.probe_port", lambda p, core=None: two[1])
    assert get_connection(None).port == 19724  # not an ambiguous-port error


def test_get_connection_on_project_less_port_is_actionable(monkeypatch):
    from archicad_mcp.connection import InstanceInfo, get_connection
    info = InstanceInfo(19723, 0, 0, None, False, None, project_open=False)
    monkeypatch.setattr("archicad_mcp.connection.probe_port", lambda p, core=None: info)
    with pytest.raises(ArchicadUnavailableError, match="no project is open"):
        get_connection(19723)


def test_refused_probe_error_names_the_modal_dialog_cause(monkeypatch):
    """Live 2026-08-31 (AC 29.0/5101): with the Object Settings dialog open,
    Archicad refuses even GetProductInfo although a project IS open, with the
    same code 4001 as the no-project case. The probe cannot tell the two
    apart, so the error must name both causes and carry the raw refusal
    instead of flatly claiming that no project is open."""
    from multiconn_archicad.errors import StandardAPIError

    class DialogBlockedCore:
        def post_command(self, *a, **k):
            raise StandardAPIError(message="Invalid program status", code=4001)

    from archicad_mcp.connection import get_connection
    info = probe_port(19723, core=DialogBlockedCore())
    assert info is not None and info.project_open is False
    monkeypatch.setattr("archicad_mcp.connection.probe_port",
                        lambda p, core=None: info)
    with pytest.raises(ArchicadUnavailableError) as excinfo:
        get_connection(19723)
    message = str(excinfo.value)
    assert "modal dialog" in message
    assert "no project is open" in message
    assert "close any open dialog" in message.lower()
    assert "code 4001: Invalid program status" in message
