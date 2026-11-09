from pathlib import Path

from archicad_mcp.tapir_install.hint import install_hint


def test_install_hint_mac():
    add_ons = Path("/Applications/Graphisoft/Archicad 30/Add-Ons")
    text = install_hint(30, "darwin", add_ons=add_ons)
    assert "TapirAddOn_AC30_Mac.zip" in text and "releases/tag/2.0.0" in text
    assert "archicad-mcp install-tapir --archicad 30" in text and "install_tapir" in text
    assert f"unzip it into {add_ons / 'Tapir'}" in text


def test_install_hint_windows():
    text = install_hint(29, "win32", detect=False)
    assert "TapirAddOn_AC29_Win.apx" in text and "copy it into" in text
    assert "the Tapir folder inside the Archicad 29 Add-Ons folder" in text


def test_install_hint_without_a_version():
    text = install_hint(None, "darwin")
    assert "TapirAddOn_AC<version>_Mac.zip" in text
    assert "archicad-mcp install-tapir" in text and "--archicad" not in text


def test_install_hint_on_an_unsupported_platform_still_points_somewhere():
    assert "releases" in install_hint(29, "linux")


def test_update_hint_says_replace():
    text = install_hint(30, "darwin", replace=True, detect=False)
    assert "archicad-mcp install-tapir --archicad 30 --replace" in text
    assert "replace_existing=true" in text and "backup" in text


def _installed(monkeypatch, tmp_path, platform: str, *, loose: bool = False):
    import sys

    from archicad_mcp.tapir_install.detect import Installation
    add_ons = tmp_path / "Archicad 29" / "Add-Ons"
    folder = add_ons if loose else add_ons / "Tapir"
    folder.mkdir(parents=True)
    (folder / "TapirAddOn_AC29_Win.apx").write_bytes(b"MZ")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr("archicad_mcp.tapir_install.hint.find_installations",
                        lambda *a, **k: [Installation(29, add_ons.parent, add_ons)])
    return folder


def test_a_tapir_that_is_there_but_not_loaded_gets_the_icacls_reset(monkeypatch, tmp_path):
    # 0.12.0 on Windows: the installed .apx had an owner-only ACL, Add-On Manager
    # said "You don't have permission to open this file", Tapir did not load.
    from archicad_mcp.tapir_install.hint import not_loaded_hint
    folder = _installed(monkeypatch, tmp_path, "win32")
    text = not_loaded_hint(29, "win32")
    assert f"has {folder / 'TapirAddOn_AC29_Win.apx'} but did not load it" in text
    assert f'icacls "{folder}" /reset /T /C' in text


def test_a_loose_apx_gets_a_reset_of_that_file_only(monkeypatch, tmp_path):
    from archicad_mcp.tapir_install.hint import not_loaded_hint
    folder = _installed(monkeypatch, tmp_path, "win32", loose=True)
    assert f'icacls "{folder / "TapirAddOn_AC29_Win.apx"}" /reset /C' in not_loaded_hint(29, "win32")


def test_no_tapir_on_disk_means_the_install_hint(monkeypatch, tmp_path):
    from archicad_mcp.tapir_install.hint import not_loaded_hint
    assert not_loaded_hint(29, "win32") is None  # conftest: no installations
    _installed(monkeypatch, tmp_path, "darwin")
    text = not_loaded_hint(29, "darwin")
    assert "Add-On Manager says why" in text and "icacls" not in text
