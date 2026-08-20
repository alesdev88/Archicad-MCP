"""Refuse a release whose version is not stated identically everywhere.

The version is written down in four places, and nothing keeps them in step:

    pyproject.toml   [project] version
    manifest.json    "version"
    README.md        the download URLs and artifact names readers paste
    server.json      the MCP registry entry: its own version, the package
                     version, and the release URL the bundle is fetched from

At release time a fifth copy of it appears as the git tag. A mismatch is
invisible while it is being made and expensive afterwards: the extension ends
up reporting a version no wheel was ever built for, the README hands new
readers a download link that 404s, and the tag that named it has already been
pushed.

server.json is the one whose drift is silent, which is why it is worth checking
even though nobody reads it by hand. The other three fail loudly when they rot:
a link 404s, an extension names a version no wheel was built for. A stale
server.json still resolves. It points the registry at the *previous* release's
bundle, which is still sitting on the releases page, so every client that
installs from the registry quietly gets an old version and nothing anywhere
reports an error.

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


def server_json_versions(data: dict) -> dict[str, str]:
    """Every version server.json states, keyed by where in the file it sits.

    Three kinds of place, checked separately because they drift separately: the
    server's own version, each package's version, and the release URL that
    package is downloaded from, which spells the version out twice over, once
    as the git tag and once inside the bundle's filename.

    The URL is read with the same patterns as the README because it is the same
    URL shape and the same artifact names, so a version appearing there is this
    project's own by construction rather than by guesswork.

    fileSha256 is deliberately not checked here. It is the one field release
    time is the first moment able to know, because it is a hash of the bundle
    that is built from the very commit that would have to contain it, so the
    release workflow stamps it after packing. Nothing that can be gated before
    a tag exists can say anything true about it.
    """
    found = {"server.json version": str(data["version"])}
    for index, package in enumerate(data.get("packages", ())):
        where = f"server.json packages[{index}]"
        found[f"{where} version"] = str(package["version"])
        # Numbered rather than collapsed onto one key. Two references in a
        # single URL that disagree with each other is exactly the corruption
        # worth catching, and keying them both as "the URL" would let the
        # second quietly overwrite the first and report a pass.
        identifier = str(package.get("identifier", ""))
        for n, (_, version) in enumerate(readme_version_refs(identifier), start=1):
            found[f"{where} URL ref {n}"] = version
    return found


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """The version each file declares, keyed by the file name so a failure can
    name the file to edit rather than the value that is wrong."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    server = json.loads((root / "server.json").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": str(pyproject["project"]["version"]),
        "manifest.json": str(manifest["version"]),
        **server_json_versions(server),
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
        print("Bring pyproject.toml, manifest.json, README.md, server.json, and "
              "the tag into agreement.")
        return 1
    # Name the README reference count rather than just the file. A regex that
    # quietly stopped matching would otherwise pass by finding nothing, and a
    # check that cannot fail is worse than no check: it reports "agree" while
    # the README rots.
    readme = ROOT / "README.md"
    refs = readme_version_refs(readme.read_text(encoding="utf-8")) if readme.exists() else []
    # Same reasoning for server.json, which has the stronger claim on it: its
    # references are read out of a structure rather than out of prose, so a
    # renamed field would not fail, it would simply stop being looked at.
    server_refs = sum(1 for key in versions if key.startswith("server.json"))
    checked = ["pyproject.toml", "manifest.json",
               f"{server_refs} server.json reference{'' if server_refs == 1 else 's'}",
               f"{len(refs)} README.md reference{'' if len(refs) == 1 else 's'}"]
    if tag is not None:
        checked.append(f"tag {tag}")
    print(f"version {versions['pyproject.toml']}: "
          f"{', '.join(checked[:-1])} and {checked[-1]} agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
