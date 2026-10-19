"""Put a checked Tapir into <Add-Ons>/Tapir/, keeping anything it replaces.

An existing Tapir is never deleted here, only moved to a backup folder outside
Add-Ons (inside Add-Ons Archicad would load it again). Every step that can fail
after something moved is undone, so a failure leaves the folder as it was.
"""
from __future__ import annotations

import errno
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from archicad_mcp.tapir_install.detect import TAPIR_SUBFOLDER, main_binary
from archicad_mcp.tapir_install.plan import MOVABLE, Row
from archicad_mcp.tapir_install.release import InstallError, check_mac_zip


class ElevationCancelled(InstallError):
    """The user dismissed the administrator password dialog."""


@dataclass
class Outcome:
    version: int
    add_ons: Path
    installed: Path | None = None
    backup: Path | None = None
    restored: bool | None = None
    error: str | None = None
    restart_note: str = ""


def backup_root(platform: str | None = None) -> Path:
    platform = platform or sys.platform
    if platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path.home() / ".local" / "share"
    return base / "archicad-mcp" / "tapir-backups"


def _extract(src: Path, dest: Path) -> None:
    # ditto keeps the bundle's permission bits and links; zipfile drops them.
    result = subprocess.run(["ditto", "-x", "-k", str(src), str(dest)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise InstallError(f"Unpacking {src.name} failed: {result.stderr.strip()}")


def stage(downloaded: Path, row: Row, work_dir: Path, *, platform: str | None = None) -> Path:
    """A private copy of the add-on, ready to move into place."""
    platform = platform or sys.platform
    dest = Path(tempfile.mkdtemp(prefix=f"stage-AC{row.inst.version}-", dir=work_dir))
    if platform != "darwin":
        staged = dest / downloaded.name
        shutil.copy2(downloaded, staged)
        return staged
    bundle_name = check_mac_zip(downloaded, row.inst.version)
    _extract(downloaded, dest)
    bundle = dest / bundle_name
    if main_binary(bundle) is None:
        raise InstallError(f"{downloaded.name} unpacked to a bundle without the "
                           "executable its Info.plist names; not installing it.")
    return bundle


def _uv_tool(executable: str) -> bool:
    parts = Path(executable).parts
    return "tools" in parts and "archicad-mcp-server" in parts


def _ps_quote(arg: str) -> str:
    return f'"{arg}"' if any(c in arg for c in " '&;()") else arg


def windows_admin_text(row: Row, *, executable: str | None = None,
                       uv_tool: bool | None = None, extra_args: list[str] = ()) -> str:
    """extra_args repeats the flags of the run that got here (--replace and
    the like), so the administrator rerun does the same thing."""
    executable = executable or sys.executable
    uv_tool = _uv_tool(executable) if uv_tool is None else uv_tool
    version = row.inst.version
    args = " ".join([f"--archicad {version}", *map(_ps_quote, extra_args)])
    # PowerShell (what Windows Terminal opens) needs the call operator in front
    # of a quoted program path; without it the line is a parse error.
    command = (f"archicad-mcp install-tapir {args}" if uv_tool else
               f'& "{executable}" -I -m archicad_mcp.tapir_install {args}')
    text = (f"Archicad {version}'s Add-Ons folder ({row.inst.add_ons}) needs "
            "administrator rights, so nothing was changed. Open PowerShell as "
            "administrator (right-click it, Run as administrator) and run: "
            f"{command}")
    if row.asset is not None:
        text += (f". Or ask IT to download {row.asset.name} from {row.asset.url}, check "
                 f"that its sha256 is {row.asset.sha256}, copy it into "
                 f"{row.inst.add_ons / TAPIR_SUBFOLDER}, and restart Archicad {version}.")
    return text


def _osascript_elevate(commands: list[list[str]]) -> None:
    """Run commands as root behind the system password dialog.

    Built only from shlex-quoted fixed paths, never from free text.
    """
    script = "set -e; " + "; ".join(shlex.join(c) for c in commands)
    applescript = 'do shell script "{}" with administrator privileges'.format(
        script.replace("\\", "\\\\").replace('"', '\\"'))
    result = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
    if result.returncode != 0:
        if "-128" in result.stderr or "canceled" in result.stderr.lower():
            raise ElevationCancelled()
        raise InstallError(f"The administrator step failed: {result.stderr.strip()}")


def _remove_empty_dirs(folder: Path | None) -> None:
    """Remove folder if nothing but empty folders are left in it. Never files."""
    if folder is None or not folder.is_dir():
        return
    for path in sorted(folder.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if not any(folder.iterdir()):
        folder.rmdir()


def _restart_note(row: Row) -> str:
    v = row.inst.version
    if row.running_version:
        return f"Archicad {v} is running: quit and reopen it to load Tapir."
    return f"Restart Archicad {v} to load Tapir."


def _mcp_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("archicad-mcp-server")
    except PackageNotFoundError:
        return "unknown"


def _write_manifest(backup: Path, row: Row, moves: list[tuple[Path, Path]], now: datetime) -> None:
    (backup / "backup.json").write_text(json.dumps({
        "archicad": row.inst.version,
        "created": now.isoformat(timespec="seconds"),
        "mcp_version": _mcp_version(),
        "entries": [{"from": str(src), "to": str(dst)} for src, dst in moves],
    }, indent=2) + "\n")


def _new_backup_folder(root: Path, version: int, now: datetime) -> Path:
    """A folder no other run uses, even two installs of one version in one second."""
    root.mkdir(parents=True, exist_ok=True)
    base = f"AC{version}-{now:%Y%m%d-%H%M%S}"
    for n in range(1, 1000):
        folder = root / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise InstallError(f"Could not create a backup folder in {root}.")


def _discard_backup(backup: Path | None) -> None:
    """Remove a backup folder that holds nothing but its own backup.json."""
    if backup is None or not backup.is_dir():
        return
    if any(p.is_file() and p.name != "backup.json" for p in backup.rglob("*")):
        return
    (backup / "backup.json").unlink(missing_ok=True)
    _remove_empty_dirs(backup)


def _cross_device(exc: OSError) -> bool:
    # Windows reports ERROR_NOT_SAME_DEVICE (17); Python maps it to EXDEV.
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def _move_out(src: Path, dst: Path) -> None:
    """Move an existing Tapir aside: a rename or nothing.

    Never shutil.move: when a rename is refused it copies and then deletes,
    and a delete that is refused halfway leaves the old Tapir gutted.
    """
    if os.path.lexists(dst):
        raise InstallError(f"{dst} already exists; not moving {src} onto it.")
    try:
        os.rename(src, dst)
    except OSError as exc:
        if _cross_device(exc):
            raise InstallError(
                f"The backup folder {dst.parent} is on a different drive than {src}, so "
                "the old Tapir cannot be moved there in one step. Nothing was changed. "
                "Move it out of the Add-Ons folder by hand, then run again.") from exc
        raise


def _remove_new_copy(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _place(staged: Path, target: Path) -> bool:
    """Move the new copy into place; True when it had to be copied instead."""
    if os.path.lexists(target):
        raise InstallError(f"{target} already exists; not overwriting it.")
    try:
        os.rename(staged, target)
        return False
    except OSError as exc:
        if not _cross_device(exc):
            raise
    try:
        if staged.is_dir():
            shutil.copytree(staged, target, symlinks=True)
        else:
            shutil.copy2(staged, target)
    except BaseException:
        if os.path.lexists(target):
            _remove_new_copy(target)  # only the partial new copy, never an old one
        raise
    return True


def apply_row(row: Row, staged: Path, *, platform: str | None = None,
              backups: Path | None = None, now: datetime | None = None,
              elevate: Callable[[list[list[str]]], None] | None = None,
              rerun_args: list[str] = ()) -> Outcome:
    platform = platform or sys.platform
    now = now or datetime.now()
    out = Outcome(row.inst.version, row.inst.add_ons, restart_note=_restart_note(row))
    tapir_dir = row.inst.add_ons / TAPIR_SUBFOLDER
    target = tapir_dir / staged.name
    entries = [e for e in row.entries if e.kind in MOVABLE] if row.action == "replace" else []
    if os.path.lexists(target) and target not in {e.path for e in entries}:
        out.error = f"{target} already exists; not overwriting it."
        return out
    backup = None
    moves: list[tuple[Path, Path]] = []
    if entries:
        backup = _new_backup_folder(backups or backup_root(platform), row.inst.version, now)
        moves = [(e.path, backup / e.path.relative_to(row.inst.add_ons)) for e in entries]
        # Written before anything moves, so a backup is never unlabelled.
        _write_manifest(backup, row, moves, now)
        out.backup = backup

    if not row.writable:
        return _apply_elevated(row, out, staged, target, backup, moves, platform, elevate,
                               rerun_args)

    done: list[tuple[Path, Path]] = []
    placed, copied, made_tapir_dir = None, False, False
    try:
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _move_out(src, dst)
            done.append((src, dst))
        if not tapir_dir.is_dir():
            tapir_dir.mkdir()
            made_tapir_dir = True
        copied = _place(staged, target)
        placed = target
    except (OSError, InstallError) as exc:
        if isinstance(exc, PermissionError) and not done and not made_tapir_dir:
            # The folder refused before anything changed: the not-writable path.
            row.writable = False
            return _apply_elevated(row, out, staged, target, backup, moves, platform, elevate,
                                   rerun_args)
        out.restored = _roll_back(done, staged, placed, copied)
        if made_tapir_dir and tapir_dir.is_dir() and not any(tapir_dir.iterdir()):
            tapir_dir.rmdir()
        if isinstance(exc, PermissionError) and platform == "win32":
            out.error = f"close Archicad {row.inst.version} and retry"
        else:
            out.error = f"Installing into Archicad {row.inst.version} failed: {exc}"
        if out.restored:
            _discard_backup(backup)
            out.backup = None
        elif backup is not None:
            out.error += (f". Putting the old Tapir back failed too: it is in {backup} "
                          f"(backup.json lists where each piece came from); move it back "
                          f"to {row.inst.add_ons} by hand.")
        return out
    out.installed = target
    return out


def _roll_back(done: list[tuple[Path, Path]], staged: Path, placed: Path | None,
               copied: bool) -> bool:
    ok = True
    if placed is not None:
        try:
            if copied:
                _remove_new_copy(placed)  # the staged original is still there
            else:
                os.rename(placed, staged)
        except OSError:
            ok = False
    for src, dst in reversed(done):
        try:
            os.rename(dst, src)
        except OSError:
            ok = False
    return ok


def _apply_elevated(row: Row, out: Outcome, staged: Path, target: Path, backup: Path | None,
                    moves: list[tuple[Path, Path]], platform: str,
                    elevate: Callable[[list[list[str]]], None] | None,
                    rerun_args: list[str] = ()) -> Outcome:
    if platform != "darwin":
        _discard_backup(backup)
        out.backup = None
        out.error = windows_admin_text(row, extra_args=rerun_args)
        return out
    elevate = elevate or _osascript_elevate
    # The backup folder is this user's; create it as this user so only the
    # moves need administrator rights.
    for _, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dst):
            out.error = f"{dst} already exists; not moving onto it."
            return out
    commands = [["mv", str(src), str(dst)] for src, dst in moves]
    commands += [["mkdir", "-p", str(target.parent)], ["mv", str(staged), str(target)]]
    if backup is not None:
        commands.append(["chown", "-R", str(os.getuid()), str(backup)])
    try:
        elevate(commands)
    except ElevationCancelled:
        _discard_backup(backup)
        out.backup = None
        out.error = "cancelled at the administrator password dialog; nothing changed"
        return out
    except InstallError as exc:
        moved = [(src, dst) for src, dst in moves if dst.exists() and not src.exists()]
        undo = [["mv", str(dst), str(src)] for src, dst in reversed(moved)]
        if target.exists() and not staged.exists():
            undo.insert(0, ["mv", str(target), str(staged)])
        out.restored = True
        if undo:
            try:
                elevate(undo)
            except InstallError:
                out.restored = False
        out.error = f"Installing into Archicad {row.inst.version} failed: {exc}"
        if out.restored:
            _discard_backup(backup)
            out.backup = None
        elif backup is not None:
            out.error += (f". Putting the old Tapir back failed too: it is in {backup}, "
                          "owned by the administrator account until it is moved back "
                          f"to {row.inst.add_ons} with administrator rights.")
        return out
    if not target.exists():
        out.error = (f"The administrator step finished but {target} is missing."
                     + (f" The old copy is in {backup}." if backup else ""))
        return out
    out.installed = target
    return out
