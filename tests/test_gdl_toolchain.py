"""Toolchain discovery across platforms: env override first, then install roots."""

import pytest

from archicad_mcp.gdl import toolchain


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_env_override_wins(tmp_path, monkeypatch):
    exe = _touch(tmp_path / "LP_XMLConverter")
    monkeypatch.setenv("LP_XMLCONVERTER", str(exe))
    assert toolchain.find_lp_xmlconverter() == exe


def test_env_override_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LP_XMLCONVERTER", str(tmp_path / "nope"))
    with pytest.raises(toolchain.ToolchainError, match="missing file"):
        toolchain.find_lp_xmlconverter()


def test_windows_layout_picks_newest(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    root = tmp_path / "GRAPHISOFT"
    _touch(root / "Archicad 28" / "LP_XMLConverter.exe")
    newest = _touch(root / "Archicad 29" / "LP_XMLConverter.exe")
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [root])
    assert toolchain.find_lp_xmlconverter() == newest


def test_macos_layout_picks_newest(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    root = tmp_path / "Graphisoft"
    rel = "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"
    _touch(root / "Archicad 28" / "Archicad 28.app" / rel)
    newest = _touch(root / "Archicad 29" / "Archicad 29.app" / rel)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [root])
    assert toolchain.find_lp_xmlconverter() == newest


def test_not_found_names_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path])
    with pytest.raises(toolchain.ToolchainError, match="LP_XMLCONVERTER"):
        toolchain.find_lp_xmlconverter()


def test_blender_windows_layout(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    exe = _touch(tmp_path / "Blender Foundation" / "Blender 4.2" / "blender.exe")
    monkeypatch.setattr(toolchain, "_blender_roots", lambda: [tmp_path])
    assert toolchain.find_blender() == exe


def test_blender_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(toolchain, "_blender_roots", lambda: [tmp_path])
    assert toolchain.find_blender() is None
