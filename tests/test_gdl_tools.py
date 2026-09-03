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
    # write_bytes, not write_text: on Windows text mode translates "\n" to
    # "\r\n", which makes the file 15 bytes longer than the string and breaks
    # the size assertion below. The tool reports the real on-disk size, so it
    # is the fixture that has to be byte-exact.
    (tmp_path / "cube.obj").write_bytes(CUBE_OBJ.encode())
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


def test_gdl_guarded_wraps_a_malformed_mesh_through_inspect(ws):
    """inspect_gdl_source never wrapped mesh_mod.load() the way build does, so
    a malformed OBJ used to escape as a raw ValueError instead of the usual
    {"error": ...} envelope. Widening _gdl_guarded's catch tuple to ValueError
    fixes it without needing a try/except in _inspect_source itself."""
    def mock_guarded(func):
        return func

    gdl_guarded = gdl_tools._gdl_guarded(mock_guarded)
    (ws.root / "bad.obj").write_text("v not_a_number\n")

    @gdl_guarded
    def inspect_bad():
        return gdl_tools._inspect_source(ws, "bad.obj")

    result = inspect_bad()
    assert isinstance(result, dict)
    assert "error" in result


def test_gdl_guarded_wraps_an_empty_mesh_through_inspect(ws):
    """An OBJ with no vertices blows up inside mesh.load()'s unit-autodetect
    (max() of an empty sequence) before _inspect_source even starts building
    its response. Same family as the malformed-mesh case above."""
    def mock_guarded(func):
        return func

    gdl_guarded = gdl_tools._gdl_guarded(mock_guarded)
    (ws.root / "empty.obj").write_text("")

    @gdl_guarded
    def inspect_empty():
        return gdl_tools._inspect_source(ws, "empty.obj")

    result = inspect_empty()
    assert isinstance(result, dict)
    assert "error" in result


def test_gdl_guarded_wraps_malformed_assets_json_through_list_sources(ws):
    """A malformed assets.json raised a raw json.JSONDecodeError out of
    list_gdl_sources (and out of build_gdl_object's read path); both go
    through the same widened _gdl_guarded catch tuple now."""
    def mock_guarded(func):
        return func

    gdl_guarded = gdl_tools._gdl_guarded(mock_guarded)
    (ws.root / "assets.json").write_text("{not valid json")

    @gdl_guarded
    def list_sources():
        return gdl_tools._list_sources(ws)

    result = list_sources()
    assert isinstance(result, dict)
    assert "error" in result


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
        if command == "AddFilesToEmbeddedLibrary":
            files = (params or {}).get("files", [])
            return {"executionResults": [{"success": True} for _ in files]}
        return {}


class FailingEmbedConn(FakeConn):
    """AddFilesToEmbeddedLibrary reports failure for the .gsm, as Tapir does
    when a file of that name is already in the embedded library."""

    def tapir(self, command, params=None):
        if command == "AddFilesToEmbeddedLibrary":
            self.calls.append((command, params))
            return {"executionResults": [{"success": False}]}
        return super().tapir(command, params)


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


def test_deploy_reports_a_failed_gsm_embed_instead_of_rendering_the_stale_one(ws):
    """Tapir cannot overwrite an existing embedded file. A second embed=true
    deploy of the same name must fail loudly, not silently reload/place/render
    the object that is already embedded under a success payload."""
    conn = FailingEmbedConn()
    with pytest.raises(gdl_tools.ToolchainError, match="embed"):
        gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                 keep=False, embed=True)
    # Must not continue on to ReloadLibraries/CreateObjects/GetElementPreviewImage.
    assert _commands(conn) == ["AddFilesToEmbeddedLibrary"]


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


class RenderFailure(Exception):
    """Stand-in for APIErrorBase to test exception type preservation."""
    pass


def test_deploy_preserves_render_exception_type_on_successful_cleanup(ws):
    """Verify original render exception type is preserved when cleanup succeeds.

    IMPORTANT: Tests that an APIErrorBase from render keeps its type (not
    converted to RuntimeError) so _guarded can apply the modal-dialog hint.
    """
    conn = FakeConn()

    # Mock to raise a RenderFailure (stands in for APIErrorBase)
    def raise_on_preview(*args, **kwargs):
        raise RenderFailure("Modal dialog blocking API")

    original_preview = deploy_mod.preview_image_bytes
    try:
        import sys
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = raise_on_preview

        # Track delete calls
        delete_called = []
        def mock_delete(conn, guids, confirm=False):
            delete_called.append((guids, confirm))

        original_delete = _mutate.delete_elements
        try:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = mock_delete

            # Call deploy with keep=False, render fails
            with pytest.raises(RenderFailure):
                gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                       keep=False, embed=False)

            # Verify delete was called
            assert delete_called, "delete_elements should have been called"
            assert delete_called[0][1] is True, "confirm should be True"

        finally:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = original_delete

    finally:
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = original_preview


def test_deploy_does_not_delete_when_keep_true_and_render_fails(ws):
    """Verify no delete occurs when keep=True and render fails.

    IMPORTANT: Ensures error message doesn't claim deletion when it didn't happen.
    """
    conn = FakeConn()

    def raise_on_preview(*args, **kwargs):
        raise RenderFailure("Render failed")

    original_preview = deploy_mod.preview_image_bytes
    try:
        import sys
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = raise_on_preview

        delete_called = []
        def mock_delete(conn, guids, confirm=False):
            delete_called.append((guids, confirm))

        original_delete = _mutate.delete_elements
        try:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = mock_delete

            # Call deploy with keep=True, render fails
            with pytest.raises(RenderFailure):
                gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                       keep=True, embed=False)

            # Verify delete was NOT called when keep=True
            assert not delete_called, "delete_elements should not be called when keep=True"

        finally:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = original_delete

    finally:
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = original_preview


def test_deploy_cleanup_failure_includes_guid(ws):
    """Verify cleanup failure includes the leaked element GUID in error.

    IMPORTANT: When both render AND cleanup fail, operator needs GUID to remove
    the orphaned element manually.
    """
    conn = FakeConn()

    def raise_on_preview(*args, **kwargs):
        raise RenderFailure("Render failed")

    original_preview = deploy_mod.preview_image_bytes
    try:
        import sys
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = raise_on_preview

        # Make delete also fail
        def mock_delete(conn, guids, confirm=False):
            raise RuntimeError("Delete also failed")

        original_delete = _mutate.delete_elements
        try:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = mock_delete

            # Call deploy with keep=False, both render and cleanup fail
            with pytest.raises(RuntimeError, match="ABC-123"):
                gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                       keep=False, embed=False)

        finally:
            sys.modules['archicad_mcp.core.mutate'].delete_elements = original_delete

    finally:
        sys.modules['archicad_mcp.gdl.deploy'].preview_image_bytes = original_preview
