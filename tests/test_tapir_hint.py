from pathlib import Path

from archicad_mcp.tapir_install.hint import install_hint


def test_install_hint_mac():
    text = install_hint(30, "darwin", add_ons=Path("/Applications/Graphisoft/Archicad 30/Add-Ons"))
    assert "TapirAddOn_AC30_Mac.zip" in text and "releases/tag/1.7.1" in text
    assert "archicad-mcp install-tapir --archicad 30" in text and "install_tapir" in text
    assert "unzip it into /Applications/Graphisoft/Archicad 30/Add-Ons/Tapir" in text


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
