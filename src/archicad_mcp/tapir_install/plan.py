"""What to do for each Archicad installation, before anything is written.

The plan is the whole safety story: an installation that already has a Tapir
is skipped unless replacing was asked for, and a Tapir this installer cannot
move aside (loaded through Add-On Manager, or only seen in a running Archicad)
is never doubled, even then.
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from archicad_mcp.tapir_install.detect import (
    TAPIR_SUBFOLDER,
    Installation,
    TapirEntry,
    default_location,
    main_binary,
    tapir_entries,
)
from archicad_mcp.tapir_install.release import Asset, Release, asset_name, sha256_of

NO_TAPIR = "no Tapir"
UP_TO_DATE = "up to date"
DIFFERENT = "different Tapir present"
NO_BUILD = "no build available"
BLOCKED = "blocked"

# Kinds that Archicad loads, and of those, the ones this installer can move.
_LOADED = {"tapir-folder", "loose", "add-on-manager", "running"}
MOVABLE = {"tapir-folder", "loose"}


@dataclass
class Row:
    inst: Installation
    asset: Asset | None
    entries: list[TapirEntry]
    identical: dict[Path, bool | None]
    state: str
    action: str
    writable: bool
    running_version: str | None = None
    notes: list[str] = field(default_factory=list)


def is_writable(folder: Path) -> bool:
    """Whether this user may add and remove entries in folder.

    os.access rather than a probe file, so that a dry run writes nothing at
    all. Where it is wrong (a Windows folder ACL), the real install meets a
    PermissionError before changing anything and takes the same path.
    """
    return os.access(folder, os.W_OK | os.X_OK)


def _reference_binary_hash(reference: Path, asset: Asset) -> str | None:
    if asset.name.endswith(".apx"):
        return asset.sha256
    bundle = asset.name.removesuffix(".zip") + ".bundle"
    member = f"{bundle}/Contents/MacOS/{bundle.removesuffix('.bundle')}"
    try:
        with zipfile.ZipFile(reference) as zf:
            return hashlib.sha256(zf.read(member)).hexdigest()
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def _identical(entry: TapirEntry, expected: str | None) -> bool | None:
    if expected is None or entry.kind == "running":
        return None
    binary = main_binary(entry.path)
    if binary is None:
        return False if entry.path.exists() else None
    try:
        return sha256_of(binary) == expected
    except OSError:
        return None


def _notes(row: Row, platform: str, running: dict[int, str] | None,
           prefs_dir: Path | None) -> None:
    on_disk = [e for e in row.entries if e.kind in MOVABLE]
    if any(e.kind == "loose" for e in row.entries):
        row.notes.append("loose in Add-Ons (Tapir's own installer deletes these "
                         "without asking; this one never does)")
    if len(on_disk) > 1:
        row.notes.append("two copies, Archicad may load Tapir twice")
    for e in row.entries:
        if e.kind == "add-on-manager":
            row.notes.append(f"loaded through Add-On Manager from {e.path}")
        elif e.kind == "disabled":
            row.notes.append(f"a Tapir at {e.path} is disabled in Add-On Manager")
    if row.state == NO_TAPIR and not (running and row.inst.version in running):
        row.notes.append("if Tapir was added through Options > Add-On Manager from "
                         "another folder, installing gives Archicad two copies; "
                         "check Add-On Manager first")
    location = default_location(row.inst, prefs_dir=prefs_dir)
    if location:
        row.notes.append(f"Add-On Manager's default location is {location}, so "
                         "Archicad may load add-ons from there too")
    if row.state == NO_BUILD:
        row.notes.append(f"the pinned release has no Tapir build for Archicad "
                         f"{row.inst.version}; --latest may have one")
    if row.action in ("install", "replace") and not row.writable:
        row.notes.append("needs an administrator password (macOS asks)"
                         if platform == "darwin" else
                         "needs an administrator terminal")


def build_plan(installations: list[Installation], release: Release, *,
               versions: set[int] | None = None, replace: bool = False,
               reference: dict[int, Path] | None = None,
               running: dict[int, str] | None = None,
               prefs_dir: Path | None = None,
               platform: str | None = None) -> list[Row]:
    platform = platform or sys.platform
    reference = reference or {}
    found = {id(i): tapir_entries(i, prefs_dir=prefs_dir) for i in installations
             if not versions or i.version in versions}
    # A running Archicad that reports Tapir is explained by any installation of
    # that version with a Tapir this installer can see; only when none has one
    # is the running Tapir somewhere unknown (and then possibly loaded by any
    # of them, so each is treated as having it).
    explained = {i.version for i in installations if id(i) in found
                 and any(e.kind in _LOADED for e in found[id(i)])}
    rows = []
    for inst in installations:
        if id(inst) not in found:
            continue
        asset = release.assets.get(asset_name(inst.version, platform))
        entries = found[id(inst)]
        running_version = (running or {}).get(inst.version)
        if running_version and inst.version not in explained:
            entries.append(TapirEntry(
                Path(f"(loaded by the running Archicad {inst.version}; "
                     "location unknown)"), "running"))
        expected = (_reference_binary_hash(reference[inst.version], asset)
                    if asset and inst.version in reference else None)
        identical = {e.path: _identical(e, expected) for e in entries if e.kind in _LOADED}
        loaded = [e for e in entries if e.kind in _LOADED]
        tapir_path = inst.add_ons / TAPIR_SUBFOLDER
        if asset is None:
            state, action = NO_BUILD, "skip"
        elif tapir_path.exists() and not tapir_path.is_dir():
            state, action = BLOCKED, "skip"
        elif not loaded:
            state, action = NO_TAPIR, "install"
        elif all(identical.get(e.path) is True for e in loaded):
            state, action = UP_TO_DATE, "nothing"
        else:
            state = DIFFERENT
            unmovable = [e for e in loaded if e.kind not in MOVABLE]
            action = "replace" if replace and not unmovable else "skip"
        row = Row(inst, asset, entries, identical, state, action,
                  writable=is_writable(inst.add_ons), running_version=running_version)
        if state == BLOCKED:
            row.notes.append(f"{tapir_path} exists and is not a folder; move it "
                             "away and run again")
        if state == DIFFERENT and action == "skip":
            if replace:
                row.notes.append("this Tapir is not in Add-Ons, so it cannot be "
                                 "moved aside; remove it in Options > Add-On Manager "
                                 "first, then run again")
            else:
                row.notes.append("left alone; pass --replace (or replace_existing=true) "
                                 "to move it to a backup folder and install the release")
        _notes(row, platform, running, prefs_dir)
        rows.append(row)
    return rows


def running_tapir_versions() -> dict[int, str]:
    """Tapir version per running Archicad version, best effort."""
    try:
        from archicad_mcp.connection import discover_instances
        return {i.version: i.tapir_version or "unknown version"
                for i in discover_instances() if i.tapir_available}
    except Exception:
        return {}
