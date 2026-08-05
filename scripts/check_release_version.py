"""Refuse a release whose version is not stated identically everywhere.

The version is written down twice, and nothing keeps the two copies in step:

    pyproject.toml   [project] version
    manifest.json    "version"

At release time a third copy of it appears as the git tag. A mismatch is
invisible while it is being made and expensive afterwards: the extension ends
up reporting a version no wheel was ever built for, and the tag that named it
has already been pushed.

Run it bare to check that the two files agree, which is what the test suite
does on every push, well before there is a tag to get wrong:

    uv run python scripts/check_release_version.py

Pass a tag to also require both files to agree with it, which is what the
release workflow does:

    uv run python scripts/check_release_version.py v0.1.0
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """The version each file declares, keyed by the file name so a failure can
    name the file to edit rather than the value that is wrong."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {
        "pyproject.toml": str(pyproject["project"]["version"]),
        "manifest.json": str(manifest["version"]),
    }


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
        print("Bring pyproject.toml, manifest.json, and the tag into agreement.")
        return 1
    where = "pyproject.toml and manifest.json"
    if tag is not None:
        where += f" and tag {tag}"
    print(f"version {versions['pyproject.toml']}: {where} agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
