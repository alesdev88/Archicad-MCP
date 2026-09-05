"""The --transport flag: stdio for local clients, http for ChatGPT-style remote ones."""
import sys

import pytest

from archicad_mcp import server as server_module
from archicad_mcp.server import main, resolve_transport


class _FakeServer:
    archicad_rule_count = 0
    archicad_rule_errors = 0
    archicad_rule_source = "bundled examples"

    def __init__(self):
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)


@pytest.fixture
def fake_server(monkeypatch):
    fake = _FakeServer()
    monkeypatch.setattr(server_module, "build_server", lambda **_: fake)
    monkeypatch.setattr(server_module, "emit_startup_banner", lambda *_: None)
    for var in ("ARCHICAD_MCP_TRANSPORT", "ARCHICAD_MCP_MODE", "ARCHICAD_MCP_RULES_DIR",
                "ARCHICAD_MCP_GDL_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    return fake


def test_resolve_transport_defaults_to_stdio_when_unset():
    assert resolve_transport(None) == "stdio"
    assert resolve_transport("") == "stdio"
    assert resolve_transport("   ") == "stdio"


def test_resolve_transport_passes_valid_values_through():
    assert resolve_transport("stdio") == "stdio"
    assert resolve_transport("http") == "http"


def test_main_runs_stdio_by_default(fake_server, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["archicad-mcp"])
    main()
    assert fake_server.run_calls == [{"transport": "stdio"}]


def test_main_http_transport_passes_host_and_listen_port(fake_server, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "archicad-mcp", "--transport", "http", "--host", "0.0.0.0", "--http-port", "9000",
    ])
    main()
    assert fake_server.run_calls == [{"transport": "http", "host": "0.0.0.0", "port": 9000}]


def test_main_http_transport_binds_to_localhost_by_default(fake_server, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["archicad-mcp", "--transport", "http"])
    main()
    assert fake_server.run_calls == [{"transport": "http", "host": "127.0.0.1", "port": 8000}]


def test_main_reads_transport_from_env(fake_server, monkeypatch):
    monkeypatch.setenv("ARCHICAD_MCP_TRANSPORT", "http")
    monkeypatch.setattr(sys, "argv", ["archicad-mcp"])
    main()
    assert fake_server.run_calls[0]["transport"] == "http"


def test_main_rejects_unknown_transport(fake_server, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["archicad-mcp", "--transport", "websocket"])
    with pytest.raises(SystemExit):
        main()
    assert fake_server.run_calls == []
