"""Find Archicad installations, their Add-Ons folders, and any Tapir already there.

The installation and Add-Ons folder detection follows Tapir's own installer
(archicad-addon/Installer/tapir_installer.py in ENZYME-APD/tapir-archicad-automation),
so both agree on where Tapir belongs:

    MIT License. Copyright (c) 2024 Enzyme APD

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions: The above copyright
    notice and this permission notice shall be included in all copies or
    substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
    WITHOUT WARRANTY OF ANY KIND.

What is ours: reading Add-On Manager preferences, so a Tapir loaded from
outside Add-Ons is seen instead of silently doubled.
"""
from __future__ import annotations

import os
import plistlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

TAPIR_SUBFOLDER = "Tapir"

# The Add-Ons folder of each Archicad language version, as Graphisoft lists
# them. Older versions spelled ArchiCAD, so names compare case-insensitively.
LOCALIZED_ADDONS_FOLDER_NAMES: tuple[str, ...] = (
    "Add-Ons",                  # AUS, AUT, CHE, CHI, DEN, GER, GRE, INT, ITA, JPN, NED, NOR, NZE, SWE, TAI, USA
    "Extensões",                # BRA, POR
    "Doplnky ArchiCADu",        # CZE
    "ArchiCAD-laajennukset",    # FIN
    "Extensions",               # FRA
    "Kiegészítök",              # HUN
    "Kiegészítők",              # HUN, alternative spelling
    "애드온",                    # KOR
    "Dodatki",                  # POL
    "Расширения ArchiCAD",      # RUS
    "Extensiones ArchiCAD",     # SPA
    "Add-On'lar",               # TUR
)
_MARKER = "XReadCfg.txt"
_MARKER_MAX_DEPTH = 2
_VERSION_IN_NAME = re.compile(r"archicad\s+(\d+)", re.IGNORECASE)
_TAPIR_FILE = re.compile(r"^TapirAddOn_.*\.(apx|bundle)$", re.IGNORECASE)
_PREFS_FILE = re.compile(r"^com\.graphisoft\.AC(?:-ARM)? (\d+)\.0\.0 .*\.plist$")
_ADDON_MANAGER_KEY = "Add-On Manager"


@dataclass
class Installation:
    version: int
    folder: Path
    add_ons: Path


@dataclass
class TapirEntry:
    path: Path
    kind: str  # "tapir-folder" | "loose" | "add-on-manager" | "disabled" | "running"
    modified: datetime | None = None
    size: int | None = None


def _norm(name: str) -> str:
    # macOS hands out decomposed Unicode names; compare composed and casefolded.
    return unicodedata.normalize("NFC", name).casefold()


_KNOWN_NAMES = {_norm(n) for n in LOCALIZED_ADDONS_FOLDER_NAMES}


def _list(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def _marker_depth(folder: Path, depth: int = 0) -> int | None:
    children = _list(folder)
    if any(c.name.lower() == _MARKER.lower() for c in children):
        return depth
    if depth >= _MARKER_MAX_DEPTH:
        return None
    found = [d for c in children if c.is_dir() and not c.is_symlink()
             if (d := _marker_depth(c, depth + 1)) is not None]
    return min(found) if found else None


def find_add_ons_folder(install_folder: Path) -> Path | None:
    candidates = [c for c in _list(install_folder)
                  if c.is_dir() and not c.name.endswith(".app")]
    for c in candidates:
        if _norm(c.name) in _KNOWN_NAMES:
            return c
    best, best_depth = None, None
    for c in candidates:
        if c.is_symlink():
            continue
        depth = _marker_depth(c)
        if depth is not None and (best_depth is None or depth < best_depth):
            best, best_depth = c, depth
    return best


def _candidate(found: dict, folder: Path, platform: str) -> None:
    match = _VERSION_IN_NAME.search(folder.name)
    if match is None or not folder.is_dir() or folder.name.endswith(".app"):
        return
    key = os.path.normcase(os.path.normpath(folder))
    if key in found:
        return
    if platform == "darwin" and not any(
            c.name.lower().startswith("archicad") and c.name.endswith(".app")
            for c in _list(folder)):
        # A copied or half-removed folder with an Add-Ons inside is not
        # something Archicad will ever load from.
        return
    add_ons = find_add_ons_folder(folder)
    if add_ons is not None:
        found[key] = Installation(int(match.group(1)), folder, add_ons)


def _scan_program_files(found: dict, program_files: Path, platform: str) -> None:
    for vendor in _list(program_files):
        if vendor.is_dir() and vendor.name.lower().startswith("graphisoft"):
            for folder in _list(vendor):
                _candidate(found, folder, platform)


def _windows_registry_folders() -> list[Path]:
    import winreg  # only on Windows

    folders: list[Path] = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    flags = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall, 0, flags) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as sub:
                        name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        if _VERSION_IN_NAME.search(str(name)):
                            folders.append(Path(winreg.QueryValueEx(sub, "InstallLocation")[0]))
                except OSError:
                    continue
    except OSError:
        pass

    # The vendor keys are undocumented and changed between versions, so walk
    # them and keep every string that names an Archicad folder.
    def walk(path: str, depth: int) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, flags) as key:
                subkeys, values = winreg.QueryInfoKey(key)[0:2]
                for i in range(values):
                    try:
                        value = winreg.EnumValue(key, i)[1]
                    except OSError:
                        continue
                    if isinstance(value, str) and _VERSION_IN_NAME.search(value):
                        folders.append(Path(value.strip('"')))
                if depth < 4:
                    for i in range(subkeys):
                        try:
                            walk(path + "\\" + winreg.EnumKey(key, i), depth + 1)
                        except OSError:
                            continue
        except OSError:
            pass

    walk(r"SOFTWARE\GRAPHISOFT", 0)
    walk(r"SOFTWARE\GRAPHISOFT SE", 0)
    return folders


def find_installations(platform: str | None = None, *, root: Path | None = None
                       ) -> list[Installation]:
    """Every Archicad installation with an Add-Ons folder, by version then folder.

    root replaces the system locations (for tests): on macOS it stands for
    /Applications, on Windows for a Program Files folder.
    """
    platform = platform or sys.platform
    found: dict[str, Installation] = {}
    if platform == "darwin":
        applications = root or Path("/Applications")
        for folder in [applications, *(c for c in _list(applications)
                                       if c.is_dir() and not c.name.endswith(".app"))]:
            for child in _list(folder):
                _candidate(found, child, platform)
    elif platform == "win32":
        if root is not None:
            _scan_program_files(found, root, platform)
        else:
            for folder in _windows_registry_folders():
                # A relative value would resolve against the working directory.
                if folder.is_absolute():
                    _candidate(found, folder, platform)
            for var in ("ProgramW6432", "ProgramFiles"):
                if os.environ.get(var):
                    _scan_program_files(found, Path(os.environ[var]), platform)
    return sorted(found.values(), key=lambda i: (i.version, i.folder.name))


def _size(path: Path) -> int | None:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return None


def _entry(path: Path, kind: str) -> TapirEntry:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        modified = None
    return TapirEntry(path, kind, modified, _size(path) if path.exists() else None)


def _url_to_path(value: str) -> Path:
    for scheme in ("lan.null2://", "file://"):
        if value.startswith(scheme):
            value = value[len(scheme):]
            break
    return Path(unquote(value))


def _prefs_files(version: int, prefs_dir: Path | None) -> list[Path]:
    if prefs_dir is None:
        if sys.platform != "darwin":
            return []
        prefs_dir = Path.home() / "Library" / "Preferences"
    return [p for p in _list(prefs_dir)
            if (m := _PREFS_FILE.match(p.name)) and int(m.group(1)) == version]


def _manager_sections(version: int, prefs_dir: Path | None) -> list[dict]:
    sections = []
    for path in _prefs_files(version, prefs_dir):
        try:
            with path.open("rb") as f:
                data = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        section = data.get(_ADDON_MANAGER_KEY) if isinstance(data, dict) else None
        if isinstance(section, dict):
            sections.append(section)
    return sections


def _strings(node, under_disable: bool = False):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, under_disable or key == "Disable")
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value, under_disable)
    elif isinstance(node, str):
        yield node, under_disable


def _inside(path: Path, folder: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(folder)) + os.sep)
    except (OSError, ValueError):
        return False


def tapir_entries(inst: Installation, *, prefs_dir: Path | None = None) -> list[TapirEntry]:
    entries: list[TapirEntry] = []
    tapir = inst.add_ons / TAPIR_SUBFOLDER
    if tapir.is_dir():
        entries += [_entry(p, "tapir-folder") for p in _list(tapir)
                    if not p.name.startswith(".")]
    entries += [_entry(p, "loose") for p in _list(inst.add_ons) if _TAPIR_FILE.match(p.name)]
    seen = set()
    for section in _manager_sections(inst.version, prefs_dir):
        for value, disabled in _strings(section):
            if "TapirAddOn_" not in value:
                continue
            path = _url_to_path(value)
            if _inside(path, inst.add_ons):
                continue
            key = (disabled, str(path))
            if key not in seen:
                seen.add(key)
                entries.append(_entry(path, "disabled" if disabled else "add-on-manager"))
    return entries


def default_location(inst: Installation, *, prefs_dir: Path | None = None) -> str | None:
    for section in _manager_sections(inst.version, prefs_dir):
        value = section.get("Default Location")
        if isinstance(value, str) and value != "%DEFAULT%":
            return str(_url_to_path(value))
    return None


def main_binary(entry_path: Path) -> Path | None:
    if entry_path.suffix.lower() == ".apx":
        return entry_path if entry_path.is_file() else None
    try:
        with (entry_path / "Contents" / "Info.plist").open("rb") as f:
            exe = plistlib.load(f).get("CFBundleExecutable")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    binary = entry_path / "Contents" / "MacOS" / str(exe)
    return binary if exe and binary.is_file() else None
