"""Put a checked Tapir into <Add-Ons>/Tapir/, keeping anything it replaces.

An existing Tapir is never deleted here, only moved to a backup folder outside
Add-Ons (inside Add-Ons Archicad would load it again). Every step that can fail
after something moved is undone, so a failure leaves the folder as it was.
"""
from __future__ import annotations

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


def windows_admin_text(row: Row, *, executable: str | None = None,
                       uv_tool: bool | None = None) -> str:
    executable = executable or sys.executable
    uv_tool = _uv_tool(executable) if uv_tool is None else uv_tool
    version = row.inst.version
    command = (f"archicad-mcp install-tapir --archicad {version}" if uv_tool else
               f'"{executable}" -I -m archicad_mcp.tapir_install --archicad {version}')
    text = (f"Archicad {version}'s Add-Ons folder ({row.inst.add_ons}) needs "
            "administrator rights, so nothing was changed. Open a terminal as "
            "administrator (right-click Terminal or PowerShell, Run as administrator) "
            f"and run: {command}")
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


def apply_row(row: Row, staged: Path, *, platform: str | None = None,
              backups: Path | None = None, now: datetime | None = None,
              elevate: Callable[[list[list[str]]], None] | None = None) -> Outcome:
    platform = platform or sys.platform
    now = now or datetime.now()
    out = Outcome(row.inst.version, row.inst.add_ons, restart_note=_restart_note(row))
    tapir_dir = row.inst.add_ons / TAPIR_SUBFOLDER
    target = tapir_dir / staged.name
    entries = [e for e in row.entries if e.kind in MOVABLE] if row.action == "replace" else []
    backup = ((backups or backup_root(platform))
              / f"AC{row.inst.version}-{now:%Y%m%d-%H%M%S}") if entries else None
    moves = [(e.path, backup / e.path.relative_to(row.inst.add_ons)) for e in entries]

    if not row.writable:
        return _apply_elevated(row, out, staged, target, backup, moves, platform, now, elevate)

    done: list[tuple[Path, Path]] = []
    placed = False
    made_tapir_dir = False
    try:
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            done.append((src, dst))
        if not tapir_dir.is_dir():
            tapir_dir.mkdir()
            made_tapir_dir = True
        if target.exists():
            raise InstallError(f"{target} already exists; not overwriting it.")
        shutil.move(staged, target)
        placed = True
    except (OSError, InstallError) as exc:
        if isinstance(exc, PermissionError) and not done:
            # The folder refused before anything changed: the not-writable path.
            _remove_empty_dirs(backup)
            row.writable = False
            return _apply_elevated(row, out, staged, target, backup, moves, platform, now, elevate)
        out.restored = _roll_back(done, staged, target if placed else None)
        if made_tapir_dir and not any(tapir_dir.iterdir()):
            tapir_dir.rmdir()
        _remove_empty_dirs(backup)
        if isinstance(exc, PermissionError) and platform == "win32":
            out.error = f"close Archicad {row.inst.version} and retry"
        else:
            out.error = f"Installing into Archicad {row.inst.version} failed: {exc}"
        return out
    if backup is not None:
        _write_manifest(backup, row, done, now)
        out.backup = backup
    out.installed = target
    return out


def _roll_back(done: list[tuple[Path, Path]], staged: Path, placed: Path | None) -> bool:
    ok = True
    if placed is not None:
        try:
            shutil.move(placed, staged)
        except OSError:
            ok = False
    for src, dst in reversed(done):
        try:
            shutil.move(dst, src)
        except OSError:
            ok = False
    return ok


def _apply_elevated(row: Row, out: Outcome, staged: Path, target: Path, backup: Path | None,
                    moves: list[tuple[Path, Path]], platform: str, now: datetime,
                    elevate: Callable[[list[list[str]]], None] | None) -> Outcome:
    if platform != "darwin":
        out.error = windows_admin_text(row)
        return out
    elevate = elevate or _osascript_elevate
    # The backup folder is this user's; create it as this user so only the
    # moves need administrator rights.
    for _, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
    commands = [["mv", str(src), str(dst)] for src, dst in moves]
    commands += [["mkdir", "-p", str(target.parent)], ["mv", str(staged), str(target)]]
    if backup is not None:
        commands.append(["chown", "-R", str(os.getuid()), str(backup)])
    try:
        elevate(commands)
    except ElevationCancelled:
        _remove_empty_dirs(backup)
        out.error = "cancelled at the administrator password dialog; nothing changed"
        return out
    except InstallError as exc:
        moved = [(src, dst) for src, dst in moves if dst.exists() and not src.exists()]
        if moved or target.exists():
            undo = [["mv", str(dst), str(src)] for src, dst in reversed(moved)]
            if target.exists() and not staged.exists():
                undo.insert(0, ["mv", str(target), str(staged)])
            try:
                elevate(undo)
                out.restored = True
            except InstallError:
                out.restored = False
        _remove_empty_dirs(backup)
        out.error = f"Installing into Archicad {row.inst.version} failed: {exc}"
        return out
    if not target.exists():
        out.error = (f"The administrator step finished but {target} is missing. "
                     + (f"The old copy is in {backup}." if backup else ""))
        return out
    if backup is not None:
        _write_manifest(backup, row, moves, now)
        out.backup = backup
    out.installed = target
    return out
