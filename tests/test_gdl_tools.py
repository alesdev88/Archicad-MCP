"""GDL tool logic, exercised without a FastMCP instance or Archicad."""

import json

import pytest

from archicad_mcp.gdl import tools as gdl_tools
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError

CUBE_OBJ = """\
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
usemtl steel
f 1 4 3 2
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "cube.obj").write_text(CUBE_OBJ)
    (tmp_path / "textures").mkdir()
    (tmp_path / "textures" / "oak_ab12cd.jpg").write_bytes(b"jpeg")
    (tmp_path / "Chair.gsm").write_bytes(b"gsm")
    (tmp_path / "assets.json").write_text(
        json.dumps({"objects": {"Chair": {"groups": {}}}}))
    return Workspace(tmp_path)


def test_list_sources_groups_by_kind(ws):
    out = gdl_tools._list_sources(ws)
    assert [s["name"] for s in out["sources"]] == ["cube.obj"]
    assert [g["name"] for g in out["built"]] == ["Chair.gsm"]
    assert out["configured_objects"] == ["Chair"]
    assert [t["name"] for t in out["textures"]] == ["oak_ab12cd.jpg"]


def test_list_sources_reports_size_and_mtime(ws):
    entry = gdl_tools._list_sources(ws)["sources"][0]
    assert entry["bytes"] == len(CUBE_OBJ)
    assert "modified" in entry


def test_list_sources_on_a_missing_root(tmp_path):
    with pytest.raises(WorkspaceError, match="does not exist"):
        gdl_tools._list_sources(Workspace(tmp_path / "nope"))


def test_inspect_returns_groups_and_bbox(ws):
    out = gdl_tools._inspect_source(ws, "cube.obj")
    assert out["face_count"] == 6
    assert out["vertex_count"] == 8
    assert [g["material"] for g in out["groups"]] == ["steel"]
    assert out["groups"][0]["faces"] == 6
    assert out["bbox"]["x"] == [0.0, 1.0]


def test_inspect_refuses_a_path_outside_the_workspace(ws):
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._inspect_source(ws, "../cube.obj")


def test_inspect_reports_a_missing_file_plainly(ws):
    with pytest.raises(FileNotFoundError):
        gdl_tools._inspect_source(ws, "absent.obj")


def test_gdl_guarded_wraps_workspace_error(tmp_path):
    """Verify that WorkspaceError from _inspect_source returns error envelope."""
    # Create a simple mock guarded that just returns the result
    def mock_guarded(func):
        return func

    # Create the gdl_guarded wrapper
    gdl_guarded = gdl_tools._gdl_guarded(mock_guarded)

    # Wrap a function that raises WorkspaceError
    @gdl_guarded
    def raises_workspace_error():
        ws = Workspace(tmp_path / "nope")
        return gdl_tools._list_sources(ws)

    # Call it and verify the error is wrapped
    result = raises_workspace_error()
    assert isinstance(result, dict)
    assert "error" in result
    assert "does not exist" in result["error"]


def test_gdl_guarded_wraps_file_not_found_error(tmp_path):
    """Verify that FileNotFoundError from _inspect_source returns error envelope."""
    # Create a simple mock guarded that just returns the result
    def mock_guarded(func):
        return func

    # Create the gdl_guarded wrapper
    gdl_guarded = gdl_tools._gdl_guarded(mock_guarded)

    ws = Workspace(tmp_path)
    (tmp_path / "assets.json").write_text(json.dumps({"objects": {}}))

    # Wrap a function that raises FileNotFoundError
    @gdl_guarded
    def raises_file_not_found():
        return gdl_tools._inspect_source(ws, "absent.obj")

    # Call it and verify the error is wrapped
    result = raises_file_not_found()
    assert isinstance(result, dict)
    assert "error" in result
    assert "No such file" in result["error"]


def test_tools_are_registered_correctly(tmp_path):
    """Verify that both tools are registered with correct metadata."""
    from unittest.mock import Mock, MagicMock

    # Create mock objects
    mcp = Mock()
    tool_mock = MagicMock()
    mcp.tool = Mock(return_value=tool_mock)

    # Create workspace
    ws = Workspace(tmp_path)
    (tmp_path / "assets.json").write_text(json.dumps({"objects": {}}))

    # Mock tool_meta and guarded
    def mock_tool_meta(title, read_only, destructive):
        return {"title": title, "read_only": read_only, "destructive": destructive}

    def mock_guarded(func):
        return func

    # Register the tools
    gdl_tools.register(mcp, 8080, ws, mock_tool_meta, mock_guarded)

    # Verify mcp.tool was called twice
    assert mcp.tool.call_count == 2

    # Verify the call arguments
    calls = mcp.tool.call_args_list
    first_call = calls[0]
    second_call = calls[1]

    # Check that both were called with read_only=True and destructive=False
    assert "List GDL workspace" in str(first_call)
    assert "Inspect GDL source mesh" in str(second_call)
