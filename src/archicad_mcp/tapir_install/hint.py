"""The one fix-it sentence every missing or outdated Tapir message ends with.

It names the exact file for this Archicad and platform, where it goes, and
both ways to have it installed, so nobody has to work that out from the
releases page.
"""
from __future__ import annotations

import sys
from pathlib import Path

from archicad_mcp.tapir_install.detect import TAPIR_SUBFOLDER, find_installations
from archicad_mcp.tapir_install.release import (
    RELEASES_URL,
    asset_name,
    load_pinned,
    platform_key,
    release_page_url,
)


def _host() -> str:
    """The platform the hint is written for; one place for tests to set it."""
    return sys.platform


def _detected_add_ons(version: int, platform: str) -> Path | None:
    if platform != sys.platform:
        return None  # another platform's folders are not on this disk
    try:
        found = [i for i in find_installations(platform) if i.version == version]
    except OSError:
        return None
    return found[0].add_ons if len(found) == 1 else None


def install_hint(archicad_version: int | None, platform: str | None = None, *,
                 add_ons: Path | None = None, detect: bool = True,
                 replace: bool = False) -> str:
    """replace=True words it for a Tapir that is there but too old: a plain
    install skips an existing Tapir, so the command needs --replace."""
    platform = platform or _host()
    try:
        key = platform_key(platform)
    except ValueError:
        return f"Install it from {RELEASES_URL}, then restart Archicad."
    release = load_pinned()
    if archicad_version is None:
        command = "archicad-mcp install-tapir"
        asset = f"TapirAddOn_AC<version>_{key}{'.zip' if key == 'Mac' else '.apx'}"
        where = "the Tapir folder inside your Archicad's Add-Ons folder"
    else:
        command = f"archicad-mcp install-tapir --archicad {archicad_version}"
        asset = asset_name(archicad_version, platform)
        if add_ons is None and detect:
            add_ons = _detected_add_ons(archicad_version, platform)
        where = (f"{add_ons / TAPIR_SUBFOLDER}" if add_ons is not None else
                 f"the Tapir folder inside the Archicad {archicad_version} Add-Ons folder")
    step = "unzip it into" if key == "Mac" else "copy it into"
    if replace:
        return (f"Update it with '{command} --replace' or the install_tapir tool with "
                "replace_existing=true (the installed copy is moved to a backup folder, "
                "and a custom Tapir build loses its own commands), or by hand: download "
                f"{asset} from {release_page_url(release)}, {step} {where} in place of "
                "the Tapir there, and restart Archicad.")
    return (f"Install it with '{command}' or the install_tapir tool, or by hand: "
            f"download {asset} from {release_page_url(release)}, {step} {where}, "
            "and restart Archicad.")
