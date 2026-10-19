import plistlib
from pathlib import Path

from archicad_mcp.tapir_install.detect import (
    Installation,
    default_location,
    find_add_ons_folder,
    find_installations,
    main_binary,
    tapir_entries,
)


def mac_install(parent: Path, name: str, version: int, add_ons: str = "Add-Ons") -> Path:
    folder = parent / name
    (folder / f"Archicad {version}.app" / "Contents").mkdir(parents=True)
    (folder / add_ons).mkdir(parents=True)
    (folder / add_ons / "XReadCfg.txt").write_text("")
    return folder


def fake_bundle(where: Path, name: str = "TapirAddOn_AC30_Mac.bundle",
                binary: bytes = b"binary") -> Path:
    bundle = where / name
    exe = name.removesuffix(".bundle")
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / exe).write_bytes(binary)
    with (bundle / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleExecutable": exe}, f)
    return bundle


def test_find_installations_mac_layout_of_this_machine(tmp_path):
    gs = tmp_path / "Graphisoft"
    mac_install(gs, "Archicad 26 Apple Silicon", 26)
    mac_install(gs, "Archicad 27", 27)
    mac_install(gs, "Archicad 30", 30)
    (gs / "Archicad Monolith Migration Libraries INT" / "Archicad Migration Libraries").mkdir(
        parents=True)
    (gs / "BIMx Desktop Viewer Apple Silicon").mkdir()
    found = find_installations("darwin", root=tmp_path)
    assert [i.version for i in found] == [26, 27, 30]
    assert found[2].add_ons == gs / "Archicad 30" / "Add-Ons"


def test_find_installations_mac_needs_the_app(tmp_path):
    folder = tmp_path / "Archicad 29"
    (folder / "Add-Ons").mkdir(parents=True)
    assert find_installations("darwin", root=tmp_path) == []


def test_find_installations_keeps_two_copies_of_one_version(tmp_path):
    mac_install(tmp_path / "Graphisoft", "Archicad 30", 30)
    mac_install(tmp_path / "Graphisoft", "Archicad 30 GER", 30)
    found = find_installations("darwin", root=tmp_path)
    assert [i.folder.name for i in found] == ["Archicad 30", "Archicad 30 GER"]


def test_add_ons_folder_localized_name(tmp_path):
    folder = tmp_path / "Archicad 29"
    (folder / "Dodatki").mkdir(parents=True)
    (folder / "Biblioteka").mkdir()
    assert find_add_ons_folder(folder) == folder / "Dodatki"


def test_add_ons_folder_by_marker(tmp_path):
    folder = tmp_path / "Archicad 29"
    (folder / "Lisaosad" / "Sub").mkdir(parents=True)
    (folder / "Lisaosad" / "Sub" / "XReadCfg.txt").write_text("")
    (folder / "Muu").mkdir()
    assert find_add_ons_folder(folder) == folder / "Lisaosad"


def test_add_ons_folder_never_inside_the_app(tmp_path):
    folder = tmp_path / "Archicad 29"
    (folder / "Archicad 29.app" / "Add-Ons").mkdir(parents=True)
    assert find_add_ons_folder(folder) is None


def test_find_installations_windows_layout(tmp_path):
    folder = tmp_path / "GRAPHISOFT" / "Archicad 29"
    (folder / "Add-Ons").mkdir(parents=True)
    (folder / "Archicad.exe").write_bytes(b"MZ")
    found = find_installations("win32", root=tmp_path)
    assert [(i.version, i.add_ons) for i in found] == [(29, folder / "Add-Ons")]


def _inst(tmp_path, version=30) -> Installation:
    folder = mac_install(tmp_path / "Graphisoft", f"Archicad {version}", version)
    return Installation(version, folder, folder / "Add-Ons")


def test_tapir_entries_none(tmp_path):
    assert tapir_entries(_inst(tmp_path), prefs_dir=tmp_path / "prefs") == []


def test_tapir_entries_folder_loose_and_both(tmp_path):
    inst = _inst(tmp_path)
    fake_bundle(inst.add_ons)
    (inst.add_ons / "Tapir").mkdir()
    fake_bundle(inst.add_ons / "Tapir")
    entries = tapir_entries(inst, prefs_dir=tmp_path / "prefs")
    assert sorted((e.kind, e.path.relative_to(inst.add_ons).as_posix()) for e in entries) == [
        ("loose", "TapirAddOn_AC30_Mac.bundle"),
        ("tapir-folder", "Tapir/TapirAddOn_AC30_Mac.bundle"),
    ]
    assert all(e.modified is not None and e.size for e in entries)


def _prefs(tmp_path, version: int, manager: dict, name: str | None = None) -> Path:
    prefs = tmp_path / "prefs"
    prefs.mkdir(exist_ok=True)
    name = name or f"com.graphisoft.AC-ARM {version}.0.0 INT v1.plist"
    with (prefs / name).open("wb") as f:
        plistlib.dump({"Add-On Manager": manager, "Other": {"x": "TapirAddOn_AC30_Mac.bundle"}}, f)
    return prefs


def test_tapir_entries_reads_add_on_manager_plist(tmp_path):
    inst = _inst(tmp_path)
    prefs = _prefs(tmp_path, 30, {
        "Additional": {"#1.": "lan.null2:///Users/x/My%20Tapir/TapirAddOn_AC30_Mac.bundle"},
        "Disable": {"#1.": "lan.null2:///Applications/Other/TapirAddOn_AC30_Mac.bundle",
                    "Disable Number": 1},
    })
    entries = tapir_entries(inst, prefs_dir=prefs)
    assert sorted((e.kind, e.path) for e in entries) == [
        ("add-on-manager", Path("/Users/x/My Tapir/TapirAddOn_AC30_Mac.bundle")),
        ("disabled", Path("/Applications/Other/TapirAddOn_AC30_Mac.bundle")),
    ]


def test_add_on_manager_entry_inside_this_add_ons_is_not_repeated(tmp_path):
    inst = _inst(tmp_path)
    fake_bundle(inst.add_ons)
    url = "lan.null2://" + (inst.add_ons / "TapirAddOn_AC30_Mac.bundle").as_posix()
    prefs = _prefs(tmp_path, 30, {"Additional": {"#1.": url}})
    assert [e.kind for e in tapir_entries(inst, prefs_dir=prefs)] == ["loose"]


def test_add_on_manager_of_another_version_is_ignored(tmp_path):
    inst = _inst(tmp_path)
    prefs = _prefs(tmp_path, 29, {"Additional": {"#1.": "lan.null2:///x/TapirAddOn_AC29_Mac.bundle"}})
    assert tapir_entries(inst, prefs_dir=prefs) == []


def test_default_location_reported_only_when_not_default(tmp_path):
    inst = _inst(tmp_path)
    assert default_location(inst, prefs_dir=_prefs(tmp_path, 30, {"Default Location": "%DEFAULT%"})) is None
    prefs = _prefs(tmp_path, 30, {"Default Location": "lan.null2:///Volumes/Office/Add-Ons"},
                   name="com.graphisoft.AC 30.0.0 INT v1.plist")
    assert default_location(inst, prefs_dir=prefs) == str(Path("/Volumes/Office/Add-Ons"))


def test_main_binary_of_bundle_and_apx(tmp_path):
    bundle = fake_bundle(tmp_path)
    assert main_binary(bundle) == bundle / "Contents" / "MacOS" / "TapirAddOn_AC30_Mac"
    apx = tmp_path / "TapirAddOn_AC29_Win.apx"
    apx.write_bytes(b"MZ")
    assert main_binary(apx) == apx
    assert main_binary(tmp_path / "missing.bundle") is None


def test_windows_registry_values_must_be_absolute(tmp_path, monkeypatch):
    import archicad_mcp.tapir_install.detect as detect_mod
    real = tmp_path / "Programs" / "GRAPHISOFT" / "Archicad 29"
    (real / "Add-Ons").mkdir(parents=True)
    # A relative value would resolve against the working directory.
    cwd = tmp_path / "cwd"
    (cwd / "Archicad 28" / "Add-Ons").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(detect_mod, "_windows_registry_folders",
                        lambda: [Path("Archicad 28"), real])
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    found = find_installations("win32")
    assert [(i.version, i.folder) for i in found] == [(29, real)]
