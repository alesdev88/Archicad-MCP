"""archicad-mcp install-tapir: install the pinned Tapir add-on into Archicad.

Exit codes: 0 done or nothing to do, 1 an error, 2 writes were needed but no
yes was given (no terminal and no --yes).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archicad_mcp.tapir_install.plan import Row
from archicad_mcp.tapir_install.release import (
    InstallError,
    Release,
    fetch_latest,
    load_pinned,
    make_client,
)
from archicad_mcp.tapir_install.run import Report, restore_hint, run

_YES = {"y", "yes"}


def _identical_word(value: bool | None) -> str:
    return {True: "identical to the release", False: "differs from the release",
            None: "unknown whether identical"}[value]


def format_plan(release: Release, rows: list[Row]) -> str:
    kind = "pinned" if release.pinned else "latest"
    lines = [f"Tapir {release.version} ({kind} release)", ""]
    if not rows:
        lines.append("No Archicad installation found. Pass --add-ons-folder and "
                     "--archicad for one in an unusual place.")
    for r in rows:
        lines.append(f"Archicad {r.inst.version}  {r.inst.folder}")
        lines.append(f"  Add-Ons: {r.inst.add_ons}")
        for e in r.entries:
            when = e.modified.strftime("%Y-%m-%d %H:%M") if e.modified else "date unknown"
            size = f"{e.size / 1e6:.1f} MB" if e.size else "size unknown"
            detail = (f"{_identical_word(r.identical.get(e.path))}"
                      if e.kind != "disabled" else "disabled")
            lines.append(f"  found ({e.kind}): {e.path}  [{when}, {size}, {detail}]")
        if r.running_version:
            lines.append(f"  running now with Tapir {r.running_version}")
        lines.append(f"  state: {r.state}  ->  {r.action}")
        lines += [f"  note: {n}" for n in r.notes]
        lines.append("")
    return "\n".join(lines)


def format_report(report: Report) -> str:
    lines = [format_plan(report.release, report.rows)]
    for o in report.outcomes:
        if o.installed:
            lines.append(f"Archicad {o.version}: installed {o.installed}")
            if o.backup:
                lines.append(f"  the old copy is in {o.backup}")
                lines.append(f"  {restore_hint(o)}")
            lines.append(f"  {o.restart_note}")
        else:
            lines.append(f"Archicad {o.version}: not installed. {o.error}")
            if o.restored is False:
                lines.append("  Undoing the partial change failed too: check the "
                             "Add-Ons folder and the backup folder by hand.")
    lines += report.notes
    return "\n".join(lines).rstrip() + "\n"


def _ask(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in _YES
    except EOFError:
        return False


def _prompting(release: Release):
    def confirm(rows: list[Row]) -> list[Row]:
        print(format_plan(release, rows))
        chosen = []
        installs = [r for r in rows if r.action == "install"]
        if installs and _ask(f"Install Tapir {release.version} into "
                             + ", ".join(f"Archicad {r.inst.version}" for r in installs) + "?"):
            chosen += installs
        for r in rows:
            if r.action == "replace" and _ask(
                    f"Archicad {r.inst.version} has the Tapir listed above. Move it to "
                    f"a backup folder and install Tapir {release.version}?"):
                chosen.append(r)
        return chosen
    return confirm


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="archicad-mcp install-tapir",
        description="Install the Tapir add-on into Archicad's Add-Ons folder. Shows "
                    "a plan first and never replaces an existing Tapir unless "
                    "--replace is given; a replaced copy is moved to a backup folder.")
    p.add_argument("--archicad", type=int, action="append", metavar="N",
                   help="only this Archicad version (repeatable)")
    p.add_argument("--dry-run", action="store_true",
                   help="download and check, then stop; writes nothing")
    p.add_argument("--replace", action="store_true",
                   help="replace an existing Tapir (moved to a backup folder)")
    p.add_argument("--yes", action="store_true",
                   help="answer yes to the prompts (never implies --replace)")
    p.add_argument("--from-file", type=Path, metavar="PATH",
                   help="use this release file instead of downloading (one --archicad)")
    p.add_argument("--latest", action="store_true",
                   help="the newest Tapir release instead of the pinned one")
    p.add_argument("--add-ons-folder", type=Path, metavar="PATH",
                   help="an Add-Ons folder detection does not find (one --archicad)")
    return p


def _release(latest: bool, inject: dict) -> Release:
    if inject.get("release") is not None:
        return inject.pop("release")
    if not latest:
        return load_pinned()
    client = inject.get("client")
    if client is not None:
        return fetch_latest(client)
    with make_client() as own:
        return fetch_latest(own)


def main(argv: list[str] | None = None, **inject) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    interactive = sys.stdin.isatty()
    try:
        release = _release(args.latest, inject)
        options = dict(versions=set(args.archicad) if args.archicad else None,
                       replace=args.replace, from_file=args.from_file,
                       add_ons_folder=args.add_ons_folder, release=release, **inject)
        if args.dry_run or (not args.yes and not interactive):
            report = run(dry_run=True, **options)
            print(format_report(report), end="")
            if args.dry_run:
                return 1 if report.errors else 0
            if any(r.action in ("install", "replace") for r in report.rows):
                print("Nothing was written. Pass --yes to install.")
                return 2
            return 0
        confirm = None if args.yes else _prompting(release)
        report = run(dry_run=False, confirm_rows=confirm, **options)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(report) if args.yes else _outcomes_only(report), end="")
    return 1 if report.errors or any(o.error for o in report.outcomes) else 0


def _outcomes_only(report: Report) -> str:
    # The plan was printed before the prompts; print only what happened.
    text = format_report(report)
    plan = format_plan(report.release, report.rows)
    return text[len(plan):].lstrip("\n") or "Nothing was installed.\n"
