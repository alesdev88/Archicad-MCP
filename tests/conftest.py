import pytest
from multiconn_archicad.errors import StandardAPIError, TapirCommandError


class FakeCore:
    """Stands in for multiconn_archicad CoreCommands. Responses keyed by command
    name; a value may be a dict or a callable(parameters) -> dict. Missing keys
    raise the same error types the real transport raises."""

    def __init__(self, official=None, tapir=None):
        self.official_responses = dict(official or {})
        self.tapir_responses = dict(tapir or {})
        self.calls: list[tuple[str, dict | None]] = []

    def _lookup(self, table, command, parameters, error_cls):
        self.calls.append((command, parameters))
        if command not in table:
            raise error_cls(message=f"FakeCore: no canned response for {command}", code=None)
        value = table[command]
        return value(parameters) if callable(value) else value

    def post_command(self, command, parameters=None, timeout=None):
        return self._lookup(self.official_responses, command, parameters, StandardAPIError)

    def post_tapir_command(self, command, parameters=None, timeout=None):
        return self._lookup(self.tapir_responses, command, parameters, TapirCommandError)


@pytest.fixture
def mac_host(monkeypatch):
    """Write Tapir install hints as on macOS, whatever the test runner is.

    On a Linux runner the hint falls back to a bare releases link (there is no
    Archicad on Linux), so tests that check the asset name and the install
    command pin the platform the hint is written for.
    """
    monkeypatch.setattr("archicad_mcp.tapir_install.hint._host", lambda: "darwin")
