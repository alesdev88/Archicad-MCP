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
    """Verify that all tools are registered with correct metadata."""
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

    # Verify mcp.tool was called three times
    assert mcp.tool.call_count == 3

    # Verify the call arguments
    calls = mcp.tool.call_args_list
    first_call = calls[0]
    second_call = calls[1]
    third_call = calls[2]

    # Check the tool names
    assert "List GDL workspace" in str(first_call)
    assert "Inspect GDL source mesh" in str(second_call)
    assert "Build GDL library part" in str(third_call)


def _fake_toolchain(monkeypatch):
    """compile_hsf writes a stub .gsm; validate_gsm reports nothing.

    LP_XMLConverter is not available in CI, and this test is about the tool's
    file handling rather than about the compiler.
    """
    def compile_hsf(hsf_dir, gsm_path):
        from pathlib import Path
        gsm = Path(gsm_path)
        gsm.write_bytes(b"stub gsm")
        return gsm

    monkeypatch.setattr(gdl_tools.toolchain, "compile_hsf", compile_hsf)
    monkeypatch.setattr(gdl_tools.toolchain, "validate_gsm", lambda *a, **k: [])


def test_build_writes_a_gsm_into_the_workspace(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    out = gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                                  decimate=True, validate=True, save_config=True)
    assert (ws.root / "Cube.gsm").is_file()
    assert out["gsm"] == "Cube.gsm"
    assert out["bytes"] == len(b"stub gsm")
    assert out["validation"] == []


def test_build_persists_the_config(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {"steel": {"label": "Frame", "rgb": [0.5, 0.5, 0.5]}}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=True, validate=True, save_config=True)
    saved = json.loads((ws.root / "assets.json").read_text())
    assert saved["objects"]["Cube"] == spec
    assert set(saved["objects"]) == {"Chair", "Cube"}


def test_build_without_save_leaves_assets_alone(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    before = (ws.root / "assets.json").read_text()
    gdl_tools._build_object(ws, "cube.obj", "Cube", config={"groups": {}},
                            decimate=True, validate=True, save_config=False)
    assert (ws.root / "assets.json").read_text() == before


def test_build_falls_back_to_the_saved_config(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {"steel": {"label": "Frame", "rgb": [0.5, 0.5, 0.5]}}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=True, validate=True, save_config=True)
    out = gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                                  decimate=True, validate=True, save_config=True)
    assert "Frame" in " ".join(out["groups"])


def test_build_refuses_a_name_that_is_not_a_filename(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "../Cube", config=None,
                                decimate=True, validate=True, save_config=False)


def test_build_removes_the_intermediate_hsf_folder(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                            decimate=True, validate=True, save_config=False)
    assert not (ws.root / "Cube").exists()


def test_build_skips_decimation_when_asked(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    called = []
    monkeypatch.setattr(gdl_tools.toolchain, "decimate",
                        lambda m, t: called.append(t) or m)
    spec = {"groups": {}, "decimate": {"steel": 100}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=False, validate=True, save_config=False)
    assert called == []
