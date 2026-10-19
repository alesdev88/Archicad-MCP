"""One install run: release, installations, download and check, plan, install.

Shared by the CLI and the install_tapir tool, so both behave the same.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from archicad_mcp.tapir_install.detect import Installation, find_installations
from archicad_mcp.tapir_install.install import Outcome, apply_row, stage
from archicad_mcp.tapir_install.plan import Row, build_plan, running_tapir_versions
from archicad_mcp.tapir_install.release import (
    InstallError,
    Release,
    asset_name,
    check_mac_zip,
    download,
    fetch_latest,
    load_pinned,
    make_client,
    verify,
)


@dataclass
class Report:
    release: Release
    rows: list[Row]
    outcomes: list[Outcome]
    dry_run: bool
    errors: dict[int, str] = field(default_factory=dict)  # download or check, per version
    checked: set[int] = field(default_factory=set)        # versions whose file matched
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def iso(d):
            return d.isoformat(timespec="seconds") if d else None
        return {
            "tapir_version": self.release.version,
            "pinned": self.release.pinned,
            "dry_run": self.dry_run,
            "installations": [{
                "archicad": r.inst.version,
                "folder": str(r.inst.folder),
                "add_ons": str(r.inst.add_ons),
                "state": r.state,
                "action": r.action,
                "asset": r.asset.name if r.asset else None,
                "sha256_checked": r.inst.version in self.checked,
                "existing": [{"path": str(e.path), "kind": e.kind, "modified": iso(e.modified),
                              "size": e.size, "identical_to_release": r.identical.get(e.path)}
                             for e in r.entries],
                "running_tapir": r.running_version,
                "writable": r.writable,
                "notes": r.notes,
            } for r in self.rows],
            "outcomes": [{
                "archicad": o.version,
                "installed": str(o.installed) if o.installed else None,
                "backup": str(o.backup) if o.backup else None,
                "restore": restore_hint(o),
                "rolled_back": o.restored,
                "error": o.error,
                "restart": o.restart_note if o.installed else None,
            } for o in self.outcomes],
            "errors": {str(v): e for v, e in self.errors.items()},
            "notes": self.notes,
        }


def restore_hint(o: Outcome) -> str | None:
    if o.backup is None:
        return None
    return (f"to restore: quit Archicad {o.version}, move what is in {o.backup} "
            f"(except backup.json) back to {o.add_ons}, and remove "
            f"{o.installed}")


def _one_version(versions: set[int] | None, flag: str) -> int:
    if not versions or len(versions) != 1:
        raise InstallError(f"{flag} needs exactly one --archicad version.")
    return next(iter(versions))


def run(*, versions: set[int] | None = None, replace: bool = False, dry_run: bool = True,
        latest: bool = False, from_file: Path | None = None, add_ons_folder: Path | None = None,
        confirm_rows: Callable[[list[Row]], list[Row]] | None = None,
        client: httpx.Client | None = None, installations: list[Installation] | None = None,
        running: dict[int, str] | None = None, platform: str | None = None,
        backups: Path | None = None, prefs_dir: Path | None = None,
        release: Release | None = None) -> Report:
    platform = platform or sys.platform
    own_client = client is None
    client = client or make_client()
    work = Path(tempfile.mkdtemp(prefix="archicad-mcp-tapir-"))
    try:
        if release is None:
            release = fetch_latest(client) if latest else load_pinned()
        if add_ons_folder is not None:
            v = _one_version(versions, "--add-ons-folder")
            installations = [Installation(v, add_ons_folder.parent, add_ons_folder)]
        elif installations is None:
            installations = find_installations(platform)
        if from_file is not None:
            v = _one_version(versions, "--from-file")
            expected = asset_name(v, platform)
            if from_file.name != expected:
                raise InstallError(f"--from-file for Archicad {v} must be the release "
                                   f"file {expected}, not {from_file.name}.")
        if running is None:
            running = running_tapir_versions()

        wanted = sorted({i.version for i in installations
                         if not versions or i.version in versions})
        reference: dict[int, Path] = {}
        errors: dict[int, str] = {}
        for v in wanted:
            asset = release.assets.get(asset_name(v, platform))
            if asset is None:
                continue
            try:
                if from_file is not None:
                    path = from_file
                else:
                    folder = work / f"download-AC{v}"
                    folder.mkdir()
                    path = download(asset, folder, client)
                verify(path, asset)
                if asset.name.endswith(".zip"):
                    check_mac_zip(path, v)
                reference[v] = path
            except InstallError as exc:
                errors[v] = str(exc)

        rows = build_plan(installations, release, versions=versions, replace=replace,
                          reference=reference, running=running, prefs_dir=prefs_dir,
                          platform=platform)
        for row in rows:
            if row.inst.version in errors and row.action in ("install", "replace"):
                row.action = "skip"
                row.notes.append(errors[row.inst.version])
        report = Report(release, rows, [], dry_run, errors, set(reference))
        if dry_run:
            report.notes.append("Dry run: the files were downloaded and checked, "
                                "nothing was written.")
            return report
        todo = [r for r in rows if r.action in ("install", "replace")]
        chosen = confirm_rows(todo) if confirm_rows is not None else todo
        for row in chosen:
            try:
                staged = stage(reference[row.inst.version], row, work, platform=platform)
                report.outcomes.append(apply_row(row, staged, platform=platform, backups=backups))
            except InstallError as exc:
                report.outcomes.append(Outcome(row.inst.version, row.inst.add_ons, error=str(exc)))
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if own_client:
            client.close()
