"""Containment: a tool argument can never name a file outside the workspace."""

import pytest

from archicad_mcp.gdl.workspace import Workspace, WorkspaceError


def test_resolves_a_plain_name(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve("chair.3ds") == (tmp_path / "chair.3ds").resolve()


def test_resolves_a_nested_name(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve("maps/oak.jpg") == (tmp_path / "maps" / "oak.jpg").resolve()


def test_root_itself_is_allowed(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve(".") == tmp_path.resolve()


def test_traversal_is_refused(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve("../secrets.txt")


def test_absolute_path_is_refused(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve(str(tmp_path / "elsewhere.txt"))


def test_symlink_out_is_refused(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    ws = Workspace(root)
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve("escape/secrets.txt")


def test_empty_name_is_refused(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError, match="Empty"):
        ws.resolve("   ")


def test_require_root_raises_when_missing(tmp_path):
    ws = Workspace(tmp_path / "nope")
    with pytest.raises(WorkspaceError, match="does not exist"):
        ws.require_root()


def test_assets_and_textures_paths(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.assets_path() == (tmp_path / "assets.json").resolve()
    assert ws.textures_dir() == (tmp_path / "textures").resolve()
