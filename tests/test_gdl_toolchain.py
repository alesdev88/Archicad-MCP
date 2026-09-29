"""Toolchain discovery across platforms: env override first, then install roots."""

import subprocess
from pathlib import Path

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


def test_blender_windows_double_digit_version(tmp_path, monkeypatch):
    """Pick Blender 10.0 over 9.0 using numeric comparison, not lexicographic sort."""
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    v9 = _touch(tmp_path / "Blender Foundation" / "Blender 9.0" / "blender.exe")
    v10 = _touch(tmp_path / "Blender Foundation" / "Blender 10.0" / "blender.exe")
    monkeypatch.setattr(toolchain, "_blender_roots", lambda: [tmp_path])
    assert toolchain.find_blender() == v10


def _mac_install(root, *versions):
    rel = "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"
    return {v: _touch(root / f"Archicad {v}" / f"Archicad {v}.app" / rel) for v in versions}


def test_version_picks_that_release(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    exes = _mac_install(tmp_path / "Graphisoft", 27, 29)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    assert toolchain.find_lp_xmlconverter(27) == exes[27]
    assert toolchain.find_lp_xmlconverter() == exes[29]


def test_version_ignores_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    exes = _mac_install(tmp_path / "Graphisoft", 27)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    monkeypatch.setenv("LP_XMLCONVERTER", str(_touch(tmp_path / "other" / "LP_XMLConverter")))
    assert toolchain.find_lp_xmlconverter(27) == exes[27]


def test_missing_version_names_installed_ones(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    _mac_install(tmp_path / "Graphisoft", 27, 29)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    with pytest.raises(toolchain.ToolchainError, match="Archicad 26.*27, 29"):
        toolchain.find_lp_xmlconverter(26)


def test_installed_versions_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    _mac_install(tmp_path / "Graphisoft", 29, 27)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    assert toolchain.installed_converter_versions() == [27, 29]


def test_compile_hsf_uses_requested_version(tmp_path, monkeypatch):
    seen = {}

    def fake_find(version=None):
        seen["version"] = version
        return tmp_path / "LP"

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"gsm")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(toolchain, "find_lp_xmlconverter", fake_find)
    monkeypatch.setattr(toolchain.subprocess, "run", fake_run)
    toolchain.compile_hsf(tmp_path / "hsf", tmp_path / "out.gsm", version=27)
    assert seen["version"] == 27
