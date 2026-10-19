import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import archicad_mcp.tapir_install.install as install_mod
from archicad_mcp.tapir_install.detect import Installation, tapir_entries
from archicad_mcp.tapir_install.install import apply_row, stage, windows_admin_text
from archicad_mcp.tapir_install.plan import build_plan
from archicad_mcp.tapir_install.release import Asset, Release
from tests.test_tapir_detect import fake_bundle
from tests.test_tapir_plan import RELEASE_BINARY, fake_release, inst, release_zip

NOW = datetime(2026, 10, 19, 12, 0, 0)


def listing(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def win_row(tmp_path, *, replace=False, writable=True, existing: bytes | None = None):
    apx = tmp_path / "dl" / "TapirAddOn_AC29_Win.apx"
    apx.parent.mkdir(parents=True)
    apx.write_bytes(b"MZ release")
    import hashlib
    asset = Asset(apx.name, apx.stat().st_size, hashlib.sha256(apx.read_bytes()).hexdigest(),
                  "https://github.com/x/releases/download/1.7.1/TapirAddOn_AC29_Win.apx")
    folder = tmp_path / "GRAPHISOFT" / "Archicad 29"
    (folder / "Add-Ons").mkdir(parents=True)
    if existing is not None:
        (folder / "Add-Ons" / "TapirAddOn_AC29_Win.apx").write_bytes(existing)
    (row,) = build_plan([Installation(29, folder, folder / "Add-Ons")],
                        Release("1.7.1", "1.7.1", {apx.name: asset}, True),
                        replace=replace, reference={29: apx},
                        prefs_dir=tmp_path / "none", platform="win32")
    row.writable = writable
    return row, apx


def test_fresh_install_windows_copy(tmp_path):
    row, apx = win_row(tmp_path)
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.error is None and out.backup is None
    assert out.installed == row.inst.add_ons / "Tapir" / "TapirAddOn_AC29_Win.apx"
    assert out.installed.read_bytes() == b"MZ release"
    assert out.restart_note == "Restart Archicad 29 to load Tapir."


def test_replace_moves_old_copies_to_backup_with_manifest(tmp_path):
    row, apx = win_row(tmp_path, replace=True, existing=b"MZ old fork")
    (row.inst.add_ons / "Tapir").mkdir()
    (row.inst.add_ons / "Tapir" / "TapirAddOn_AC29_Win.apx").write_bytes(b"MZ older")
    row.entries = tapir_entries(row.inst, prefs_dir=tmp_path / "none")
    assert row.action == "replace"
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.error is None
    backup = tmp_path / "b" / "AC29-20261019-120000"
    assert out.backup == backup
    assert listing(backup) == ["Tapir", "Tapir/TapirAddOn_AC29_Win.apx",
                               "TapirAddOn_AC29_Win.apx", "backup.json"]
    assert (backup / "TapirAddOn_AC29_Win.apx").read_bytes() == b"MZ old fork"
    manifest = json.loads((backup / "backup.json").read_text())
    assert manifest["archicad"] == 29
    assert {Path(e["from"]).name for e in manifest["entries"]} == {"TapirAddOn_AC29_Win.apx"}
    assert listing(row.inst.add_ons) == ["Tapir", "Tapir/TapirAddOn_AC29_Win.apx"]
    assert out.installed.read_bytes() == b"MZ release"


def test_install_rolls_back_when_the_move_in_fails(tmp_path, monkeypatch):
    row, apx = win_row(tmp_path, replace=True, existing=b"MZ old fork")
    before = listing(row.inst.add_ons)
    real_move = shutil.move

    def move(src, dst):
        if Path(src) == apx:
            raise OSError("disk full")
        return real_move(src, dst)

    monkeypatch.setattr(install_mod.shutil, "move", move)
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.restored is True and "disk full" in out.error and out.installed is None
    assert listing(row.inst.add_ons) == before
    assert (row.inst.add_ons / "TapirAddOn_AC29_Win.apx").read_bytes() == b"MZ old fork"


def test_windows_locked_apx_says_close_archicad(tmp_path, monkeypatch):
    row, apx = win_row(tmp_path, replace=True, existing=b"MZ old fork")
    real_move = shutil.move

    def move(src, dst):
        if Path(src) == apx:
            raise PermissionError("in use")
        return real_move(src, dst)

    monkeypatch.setattr(install_mod.shutil, "move", move)
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.error == "close Archicad 29 and retry" and out.restored is True


def test_windows_not_writable_changes_nothing_and_gives_the_command(tmp_path):
    row, apx = win_row(tmp_path, writable=False)
    before = listing(row.inst.add_ons)
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert listing(row.inst.add_ons) == before and out.installed is None
    assert "-I -m archicad_mcp.tapir_install --archicad 29" in out.error
    assert row.asset.sha256 in out.error
    assert str(row.inst.add_ons / "Tapir") in out.error


def test_windows_admin_text_for_a_uv_tool_install(tmp_path):
    row, _ = win_row(tmp_path)
    text = windows_admin_text(row, uv_tool=True)
    assert "archicad-mcp install-tapir --archicad 29" in text


def test_windows_permission_error_on_first_step_gives_the_command(tmp_path, monkeypatch):
    # os.access said writable, the folder ACL disagreed: nothing changed.
    row, apx = win_row(tmp_path)

    def mkdir(self, *a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(install_mod.Path, "mkdir", mkdir)
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.installed is None and "administrator" in out.error


def _mac_row(tmp_path, *, replace=False, writable=True):
    zipped = release_zip(tmp_path / "rel", 30)
    i = inst(tmp_path, 30)
    if replace:
        fake_bundle(i.add_ons, "TapirAddOn_AC30_Mac.bundle", b"a fork")
    (row,) = build_plan([i], fake_release({30: zipped}), replace=replace,
                        reference={30: zipped}, prefs_dir=tmp_path / "none", platform="darwin")
    row.writable = writable
    return row, zipped


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto is macOS only")
def test_fresh_install_mac_ditto(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bundle = fake_bundle(src, "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    exe = bundle / "Contents" / "MacOS" / "TapirAddOn_AC30_Mac"
    exe.chmod(0o755)
    zipped = tmp_path / "rel" / "TapirAddOn_AC30_Mac.zip"
    zipped.parent.mkdir()
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(bundle), str(zipped)], check=True)
    i = inst(tmp_path, 30)
    (row,) = build_plan([i], fake_release({30: zipped}), replace=False,
                        reference={30: zipped}, prefs_dir=tmp_path / "none", platform="darwin")
    work = tmp_path / "work"
    work.mkdir()
    staged = stage(zipped, row, work, platform="darwin")
    out = apply_row(row, staged, platform="darwin", backups=tmp_path / "b", now=NOW)
    installed = i.add_ons / "Tapir" / "TapirAddOn_AC30_Mac.bundle"
    assert out.error is None and out.installed == installed
    assert os.access(installed / "Contents" / "MacOS" / "TapirAddOn_AC30_Mac", os.X_OK)


def test_stage_refuses_a_bundle_without_its_executable(tmp_path, monkeypatch):
    row, zipped = _mac_row(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(install_mod, "_extract", lambda src, dest: (dest / "TapirAddOn_AC30_Mac.bundle").mkdir())
    with pytest.raises(Exception, match="executable"):
        stage(zipped, row, work, platform="darwin")


@pytest.mark.skipif(sys.platform == "win32", reason="runs mv and chown")
def test_mac_not_writable_uses_elevation_runner(tmp_path):
    row, _ = _mac_row(tmp_path, replace=True, writable=False)
    staged = fake_bundle(tmp_path / "my stage", "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    calls = []

    def elevate(commands):
        calls.append(commands)
        for argv in commands:  # run them as this user to prove the quoting
            subprocess.run(argv, check=True)

    out = apply_row(row, staged, platform="darwin", backups=tmp_path / "b k", now=NOW,
                    elevate=elevate)
    assert out.error is None, out.error
    (commands,) = calls
    assert commands[-1][:2] == ["chown", "-R"]
    assert commands[-1][-1] == str(tmp_path / "b k" / "AC30-20261019-120000")
    assert (row.inst.add_ons / "Tapir" / "TapirAddOn_AC30_Mac.bundle").is_dir()
    assert (tmp_path / "b k" / "AC30-20261019-120000" / "backup.json").is_file()


def test_mac_cancelled_dialog_changes_nothing(tmp_path):
    row, _ = _mac_row(tmp_path, writable=False)
    staged = fake_bundle(tmp_path / "stage", "TapirAddOn_AC30_Mac.bundle", RELEASE_BINARY)
    before = listing(row.inst.add_ons)

    def elevate(commands):
        raise install_mod.ElevationCancelled()

    out = apply_row(row, staged, platform="darwin", backups=tmp_path / "b", now=NOW,
                    elevate=elevate)
    assert out.error == "cancelled at the administrator password dialog; nothing changed"
    assert listing(row.inst.add_ons) == before


def test_restart_note_when_archicad_is_running(tmp_path):
    row, apx = win_row(tmp_path)
    row.running_version = "1.7.1"
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.restart_note == "Archicad 29 is running: quit and reopen it to load Tapir."


def test_install_never_overwrites_an_existing_target(tmp_path):
    row, apx = win_row(tmp_path)
    (row.inst.add_ons / "Tapir").mkdir()
    (row.inst.add_ons / "Tapir" / "TapirAddOn_AC29_Win.apx").write_bytes(b"MZ surprise")
    out = apply_row(row, apx, platform="win32", backups=tmp_path / "b", now=NOW)
    assert out.installed is None and out.error
    assert (row.inst.add_ons / "Tapir" / "TapirAddOn_AC29_Win.apx").read_bytes() == b"MZ surprise"
