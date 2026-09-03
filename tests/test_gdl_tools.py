"""GDL tool logic, exercised without a FastMCP instance or Archicad."""

import base64
import json

import pytest

from archicad_mcp.core import mutate as _mutate
from archicad_mcp.gdl import deploy as deploy_mod
from archicad_mcp.gdl import tools as gdl_tools
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError

PNG = b"\x89PNG\r\n\x1a\nfake"

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

    # Verify mcp.tool was called four times
    assert mcp.tool.call_count == 4

    # Verify the call arguments
    calls = mcp.tool.call_args_list
    first_call = calls[0]
    second_call = calls[1]
    third_call = calls[2]
    fourth_call = calls[3]

    # Check the tool names
    assert "List GDL workspace" in str(first_call)
    assert "Inspect GDL source mesh" in str(second_call)
    assert "Build GDL library part" in str(third_call)
    assert "Deploy GDL library part" in str(fourth_call)


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


def test_build_rejects_absolute_source_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"source": "/etc/passwd", "groups": {}}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_traversal_in_source_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"source": "../../etc/passwd", "groups": {}}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_absolute_texture_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {}, "textures": {"logo": "/etc/passwd"}}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_traversal_in_texture_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {}, "textures": {"logo": "../outside/secret.txt"}}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_absolute_variant_role_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {}, "variants": [{"label": "Test",
                                        "roles": {"face": "/etc/passwd"}}]}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_traversal_in_variant_role_path(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {}, "variants": [{"label": "Test",
                                        "roles": {"face": "../outside/secret.txt"}}]}
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                decimate=True, validate=True, save_config=False)


def test_build_rejects_poisoned_assets_json(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {}, "textures": {"logo": "textures/oak_ab12cd.jpg"}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=True, validate=True, save_config=True)
    poisoned = json.loads((ws.root / "assets.json").read_text())
    poisoned["objects"]["Cube"]["textures"]["logo"] = "../outside/secret.txt"
    (ws.root / "assets.json").write_text(json.dumps(poisoned))
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                                decimate=True, validate=True, save_config=False)


def test_build_handles_malformed_source_mesh(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    (ws.root / "bad.obj").write_text("v not_a_number\n")
    with pytest.raises(gdl_tools.MeshParseError):
        gdl_tools._build_object(ws, "bad.obj", "BadMesh", config=None,
                                decimate=True, validate=True, save_config=False)


def test_build_handles_corrupted_assets_json_on_save(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    (ws.root / "assets.json").write_text("{invalid json")
    spec = {"groups": {}}
    result = gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                     decimate=True, validate=True, save_config=True)
    assert result["config_saved"] is False
    assert "config_save_error" in result
    assert result["gsm"] == "Cube.gsm"


def test_build_handles_assets_json_as_directory(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    (ws.root / "assets.json").unlink()
    (ws.root / "assets.json").mkdir()
    spec = {"groups": {}}
    result = gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                                     decimate=True, validate=True, save_config=True)
    assert result["config_saved"] is False
    assert "config_save_error" in result
    assert result["gsm"] == "Cube.gsm"


class FakeConn:
    port = 19723

    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append((command, params))
        if command == "GetElementPreviewImage":
            return {"previewImage": base64.b64encode(PNG).decode()}
        if command == "CreateObjects":
            return {"elements": [{"elementId": {"guid": "ABC-123"}}]}
        return {}


def _commands(conn):
    return [c for c, _ in conn.calls]


def test_deploy_reloads_places_renders_and_deletes(ws):
    conn = FakeConn()
    payload, png = gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                            keep=False, embed=False)
    assert _commands(conn) == ["ReloadLibraries", "CreateObjects",
                               "GetElementPreviewImage", "DeleteElements"]
    assert png == PNG
    assert payload["kept"] is False
    assert payload["element_guid"] == "ABC-123"


def test_deploy_with_keep_does_not_delete(ws):
    conn = FakeConn()
    payload, _ = gdl_tools._deploy_object(ws, conn, "Chair", place=(1.0, 2.0),
                                          keep=True, embed=False)
    assert "DeleteElements" not in _commands(conn)
    assert payload["kept"] is True


def test_deploy_places_at_the_requested_coordinates(ws):
    conn = FakeConn()
    gdl_tools._deploy_object(ws, conn, "Chair", place=(1.5, -2.5), keep=True,
                             embed=False)
    params = dict(conn.calls)["CreateObjects"]
    assert params["objectsData"][0]["coordinates"] == {"x": 1.5, "y": -2.5, "z": 0.0}
    assert params["objectsData"][0]["libraryPartName"] == "Chair"


def test_deploy_embeds_when_asked(ws):
    conn = FakeConn()
    gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0), keep=False,
                             embed=True)
    assert _commands(conn)[0] == "AddFilesToEmbeddedLibrary"


def test_deploy_refuses_a_missing_gsm(ws):
    conn = FakeConn()
    with pytest.raises(FileNotFoundError, match="Absent.gsm"):
        gdl_tools._deploy_object(ws, conn, "Absent", place=(0.0, 0.0),
                                 keep=False, embed=False)


def test_deploy_refuses_a_name_outside_the_workspace(ws):
    conn = FakeConn()
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._deploy_object(ws, conn, "../Chair", place=(0.0, 0.0),
                                 keep=False, embed=False)


@pytest.mark.asyncio
async def test_deploy_gdl_object_through_client(ws):
    """Integration test: verify the tool works end-to-end through FastMCP client.

    This test catches the CRITICAL issue where a -> list return annotation
    causes FastMCP to generate an outputSchema that rejects Image content.
    """
    import fastmcp
    from mcp.types import ImageContent

    # Create FastMCP instance
    mcp = fastmcp.FastMCP("test", "1.0.0")

    # Mock tool_meta to return the correct format for FastMCP
    def mock_tool_meta(title, read_only, destructive):
        return {
            "title": title,
            "annotations": {
                "title": title,
                "readOnlyHint": read_only,
                "destructiveHint": destructive
            }
        }

    def mock_guarded(func):
        return func

    # Register the tools with a FakeConn that will be used for all calls
    fake_conn_instance = FakeConn()

    # We need to patch get_connection to return our FakeConn
    original_get_connection = gdl_tools.get_connection
    try:
        gdl_tools.get_connection = lambda port: fake_conn_instance

        gdl_tools.register(mcp, 8080, ws, mock_tool_meta, mock_guarded)

        # Create a client and call the tool
        async with fastmcp.Client(mcp) as client:
            result = await client.call_tool("deploy_gdl_object", {
                "name": "Chair",
                "x": 0.0,
                "y": 0.0,
                "keep": False,
                "embed": False
            })

            # Verify the result has content blocks
            assert hasattr(result, 'content'), "Result should have content blocks"
            assert len(result.content) >= 2, "Should have at least payload and image"

            # Verify the first content block is the payload (TextContent)
            payload_block = result.content[0]
            assert hasattr(payload_block, 'type'), "First block should have a type"
            assert payload_block.type == "text", "First block should be TextContent"

            # Verify the second content block is the image (ImageContent)
            image_block = result.content[1]
            assert isinstance(image_block, ImageContent), (
                f"Second block should be ImageContent, got {type(image_block)}")
            # MCP base64-encodes image data
            assert base64.b64decode(image_block.data) == PNG, "Image data should match"
            assert image_block.mimeType == "image/png", "Image mime type should be image/png"

    finally:
        # Restore original get_connection
        gdl_tools.get_connection = original_get_connection


def test_deploy_handles_render_failure_with_cleanup(ws):
    """Verify cleanup happens even if render fails."""
    conn = FakeConn()

    # Make preview_image_bytes raise an exception
    def raise_on_preview(*args, **kwargs):
        raise RuntimeError("Simulated render failure")

    original_preview = deploy_mod.preview_image_bytes
    try:
        import sys
        # Patch at the module level
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = raise_on_preview

        # Mock delete_elements to track if it was called
        delete_called = []
        def mock_delete(conn, guids, confirm=False):
            delete_called.append((guids, confirm))

        original_delete = _mutate.delete_elements
        try:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = mock_delete

            # Call deploy and expect failure but with cleanup
            with pytest.raises(RuntimeError, match="Failed to capture preview"):
                gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                       keep=False, embed=False)

            # Verify delete was called despite the render failure
            assert delete_called, "delete_elements should have been called"
            assert delete_called[0][1] is True, "confirm should be True"

        finally:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = original_delete

    finally:
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = original_preview
