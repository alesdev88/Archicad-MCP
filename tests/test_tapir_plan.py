import hashlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

from archicad_mcp.tapir_install.detect import Installation
from archicad_mcp.tapir_install.plan import (
    BLOCKED,
    DIFFERENT,
    NO_BUILD,
    NO_TAPIR,
    UP_TO_DATE,
    build_plan,
)
from archicad_mcp.tapir_install.release import Asset, Release
from tests.test_tapir_detect import fake_bundle, mac_install

RELEASE_BINARY = b"tapir 1.7.1 binary"


def release_zip(tmp_path: Path, version: int = 30, binary: bytes = RELEASE_BINARY) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    bundle = fake_bundle(src, f"TapirAddOn_AC{version}_Mac.bundle", binary)
    path = tmp_path / f"TapirAddOn_AC{version}_Mac.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for p in sorted(bundle.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return path


def fake_release(zips: dict[int, Path]) -> Release:
    assets = {}
    for version, path in zips.items():
        data = path.read_bytes()
        name = path.name
        assets[name] = Asset(name, len(data), hashlib.sha256(data).hexdigest(), f"https://x/{name}")
    return Release("1.7.1", "1.7.1", assets, pinned=True)


def inst(tmp_path: Path, version: int, name: str | None = None) -> Installation:
    folder = mac_install(tmp_path / "Applications" / "Graphisoft",
                         name or f"Archicad {version}", version)
    return Installation(version, folder, folder / "Add-Ons")


def plan(tmp_path, installations, zips, **kw):
    kw.setdefault("reference", {v: p for v, p in zips.items()})
    return build_plan(installations, fake_release(zips), prefs_dir=tmp_path / "noprefs",
                      platform="darwin", **kw)


def test_plan_installs_where_missing_and_skips_others(tmp_path):
    zips = {v: release_zip(tmp_path / f"r{v}", v) for v in (28, 29, 30)}
    missing, different, same = inst(tmp_path, 28), inst(tmp_path, 29), inst(tmp_path, 30)
    fake_bundle(different.add_ons, "TapirAddOn_AC29_Mac.bundle", b"a fork")
    (same.add_ons / "Tapir").mkdir()
    fake_bundle(same.add_ons / "Tapir", "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    rows = plan(tmp_path, [missing, different, same], zips, replace=False)
    assert [(r.state, r.action) for r in rows] == [
        (NO_TAPIR, "install"), (DIFFERENT, "skip"), (UP_TO_DATE, "nothing")]
    assert any("loose in Add-Ons" in n for n in rows[1].notes)
    assert list(rows[2].identical.values()) == [True]
    assert list(rows[1].identical.values()) == [False]


def test_plan_replace_turns_skip_into_replace(tmp_path):
    zips = {29: release_zip(tmp_path / "r", 29)}
    i = inst(tmp_path, 29)
    fake_bundle(i.add_ons, "TapirAddOn_AC29_Mac.bundle", b"a fork")
    (row,) = plan(tmp_path, [i], zips, replace=True)
    assert (row.state, row.action) == (DIFFERENT, "replace")


def test_plan_two_copies_note(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    i = inst(tmp_path, 30)
    fake_bundle(i.add_ons, "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    (i.add_ons / "Tapir").mkdir()
    fake_bundle(i.add_ons / "Tapir", "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    (row,) = plan(tmp_path, [i], zips, replace=False)
    assert any("two copies" in n for n in row.notes)


def test_plan_versions_narrows_rows(tmp_path):
    zips = {v: release_zip(tmp_path / f"r{v}", v) for v in (28, 30)}
    rows = plan(tmp_path, [inst(tmp_path, 28), inst(tmp_path, 30), inst(tmp_path, 30, "Archicad 30 GER")],
                zips, versions={30}, replace=False)
    assert [r.inst.folder.name for r in rows] == ["Archicad 30", "Archicad 30 GER"]


def test_plan_no_build_for_archicad_31(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    (row,) = plan(tmp_path, [inst(tmp_path, 31)], zips, replace=False)
    assert (row.state, row.action, row.asset) == (NO_BUILD, "skip", None)
    assert any("--latest" in n for n in row.notes)


def test_plan_without_reference_says_unknown(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    i = inst(tmp_path, 30)
    bundle = fake_bundle(i.add_ons, "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    (row,) = plan(tmp_path, [i], zips, replace=False, reference={})
    assert row.identical == {bundle: None}
    assert row.state == DIFFERENT


def test_plan_running_tapir_without_files_is_different(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    (row,) = plan(tmp_path, [inst(tmp_path, 30)], zips, replace=True, running={30: "1.5.10"})
    assert row.state == DIFFERENT and row.running_version == "1.5.10"
    assert [e.kind for e in row.entries] == ["running"]
    # It cannot be moved aside, so even --replace leaves it for the user.
    assert row.action == "skip"


def test_plan_refuses_when_tapir_is_a_file(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    i = inst(tmp_path, 30)
    (i.add_ons / "Tapir").write_text("not a folder")
    (row,) = plan(tmp_path, [i], zips, replace=True)
    assert (row.state, row.action) == (BLOCKED, "skip")


def test_plan_warns_about_add_on_manager_when_nothing_is_running(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    (row,) = plan(tmp_path, [inst(tmp_path, 30)], zips, replace=False, running={})
    assert any("Add-On Manager" in n for n in row.notes)


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions, not root")
def test_plan_not_writable_note(tmp_path):
    zips = {30: release_zip(tmp_path / "r", 30)}
    i = inst(tmp_path, 30)
    i.add_ons.chmod(0o555)
    try:
        (row,) = plan(tmp_path, [i], zips, replace=False)
    finally:
        i.add_ons.chmod(0o755)
    assert row.writable is False
    assert any("administrator password" in n for n in row.notes)


def test_plan_windows_not_writable_note(tmp_path, monkeypatch):
    apx = tmp_path / "TapirAddOn_AC29_Win.apx"
    apx.write_bytes(b"MZ release")
    asset = Asset(apx.name, apx.stat().st_size, hashlib.sha256(apx.read_bytes()).hexdigest(), "https://x")
    rel = Release("1.7.1", "1.7.1", {apx.name: asset}, True)
    folder = tmp_path / "GRAPHISOFT" / "Archicad 29"
    (folder / "Add-Ons").mkdir(parents=True)
    monkeypatch.setattr("archicad_mcp.tapir_install.plan.is_writable", lambda p: False)
    (row,) = build_plan([Installation(29, folder, folder / "Add-Ons")], rel, replace=False,
                        reference={29: apx}, prefs_dir=tmp_path / "none", platform="win32")
    assert row.action == "install" and row.writable is False
    assert any("administrator terminal" in n for n in row.notes)


def test_running_tapir_is_explained_by_another_copy_on_disk(tmp_path):
    # Two Archicad 30 folders; the running one loads the bundle in the first.
    zips = {30: release_zip(tmp_path / "r", 30)}
    with_tapir, empty = inst(tmp_path, 30), inst(tmp_path, 30, "Archicad 30 GER")
    fake_bundle(with_tapir.add_ons, "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    rows = plan(tmp_path, [with_tapir, empty], zips, replace=False, running={30: "1.7.1"})
    assert [(r.state, r.action) for r in rows] == [(UP_TO_DATE, "nothing"), (NO_TAPIR, "install")]
