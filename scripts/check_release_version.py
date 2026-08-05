"""Refuse a release whose version is not stated identically everywhere.

The version is written down in three places, and nothing keeps them in step:

    pyproject.toml   [project] version
    manifest.json    "version"
    README.md        the download URLs and artifact names readers paste

At release time a fourth copy of it appears as the git tag. A mismatch is
invisible while it is being made and expensive afterwards: the extension ends
up reporting a version no wheel was ever built for, the README hands new
readers a download link that 404s, and the tag that named it has already been
pushed.

Run it bare to check that the files agree, which is what the test suite does on
every push, well before there is a tag to get wrong:

    uv run python scripts/check_release_version.py

Pass a tag to also require them to agree with it, which is what the release
workflow does:

    uv run python scripts/check_release_version.py v0.1.0
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The README hands readers commands to paste. Every one of them that carries a
# version is a broken install the moment the project moves on and the README
# does not, so they are checked too.
#
# These patterns deliberately anchor on this project's own artifact names and
# release URL shape rather than looking for anything version-shaped. The README
# is full of other people's version numbers (Archicad 29, Tapir 1.5.3, Python
# 3.12, mcpb 2.1.2, ports 19723-19743), and a scanner that could not tell those
# apart would block every release the moment Graphisoft shipped an update.
README_PATTERNS = (
    # The tag inside a release download URL.
    re.compile(r"/releases/download/v(?P<version>[^/\s)]+)/"),
    # The version inside a built artifact's filename.
    re.compile(r"archicad[-_]mcp-(?P<version>\d[^\s\"')]*?)"
               r"(?:-py3-none-any\.whl|\.tar\.gz|\.mcpb)"),
    # A source install pinned to a release tag.
    re.compile(r"Archicad-MCP\.git@v(?P<version>\S+)"),
)


def readme_version_refs(text: str) -> list[tuple[int, str]]:
    """Every (line number, version) this project's own name appears with.

    Takes the text rather than a path so the false-positive behaviour, which is
    the part that decides whether this check is safe to gate a release on, can
    be tested against arbitrary content instead of only against the real file.
    """
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in README_PATTERNS:
            found.extend((lineno, m.group("version")) for m in pattern.finditer(line))
    return found


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """The version each file declares, keyed by the file name so a failure can
    name the file to edit rather than the value that is wrong."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": str(pyproject["project"]["version"]),
        "manifest.json": str(manifest["version"]),
    }
    readme = root / "README.md"
    if readme.exists():
        # Grouped by version, so a README that is simply correct contributes one
        # line rather than one per mention, and a README that is wrong points at
        # every line that has to change.
        lines_by_version: dict[str, list[int]] = {}
        for lineno, version in readme_version_refs(readme.read_text(encoding="utf-8")):
            lines_by_version.setdefault(version, []).append(lineno)
        for version, linenos in lines_by_version.items():
            where = ", ".join(str(n) for n in sorted(set(linenos)))
            versions[f"README.md line{'s' if len(set(linenos)) > 1 else ''} {where}"] = version
    return versions


def normalise_tag(tag: str) -> str:
    """The version a tag names. Accepts the bare tag name ("v0.1.0") and the
    full ref ("refs/tags/v0.1.0"), so a caller can hand over whichever of
    GITHUB_REF_NAME and GITHUB_REF it happens to have without the difference
    turning into a spurious mismatch."""
    name = tag.strip().removeprefix("refs/tags/")
    return name[1:] if name[:1] in {"v", "V"} else name


def disagreements(versions: dict[str, str], tag: str | None = None) -> list[str]:
    """Every place the version is stated, as lines to print, when they do not
    all state the same thing. Empty means the release can go ahead.

    It reports all of them rather than the first difference it finds, because
    a version bump that missed one file has usually missed the same file for a
    reason, and one run should say everything that has to change before the
    tag is pushed again.
    """
    stated = dict(versions)
    if tag is not None:
        stated[f"tag {tag}"] = normalise_tag(tag)
    if len(set(stated.values())) < 2:
        return []
    return [f"{where} says {version}" for where, version in sorted(stated.items())]


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print(__doc__)
        return 2
    tag = argv[0] if argv else None
    versions = read_versions()
    problems = disagreements(versions, tag)
    if problems:
        print("Version mismatch, refusing to release:")
        for line in problems:
            print(f"  {line}")
        print("Bring pyproject.toml, manifest.json, README.md, and the tag into "
              "agreement.")
        return 1
    # Name the README reference count rather than just the file. A regex that
    # quietly stopped matching would otherwise pass by finding nothing, and a
    # check that cannot fail is worse than no check: it reports "agree" while
    # the README rots.
    readme = ROOT / "README.md"
    refs = readme_version_refs(readme.read_text(encoding="utf-8")) if readme.exists() else []
    checked = ["pyproject.toml", "manifest.json",
               f"{len(refs)} README.md reference{'' if len(refs) == 1 else 's'}"]
    if tag is not None:
        checked.append(f"tag {tag}")
    print(f"version {versions['pyproject.toml']}: "
          f"{', '.join(checked[:-1])} and {checked[-1]} agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
