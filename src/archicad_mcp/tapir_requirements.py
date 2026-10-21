"""Which Tapir each tool needs, checked before the tool sends anything.

A tool's minimum version is computed from the "since" versions in the gateway
registry, never typed in. The check itself asks Archicad whether the one
command that sets that minimum is registered, because version numbers cannot
judge a custom build: the Archicad 29 fork reports 1.5.10 and has 1.6.0
commands.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from archicad_mcp.connection import ArchicadConnection, tapir_missing_message

# The Tapir commands every call of each tool needs. A command only some
# arguments use stays out (require_tapir_command covers those where they are
# chosen), and so do tools that degrade without Tapir. create_elements checks
# itself, per element type, in core/create.py.
# modify_elements checks itself: Tapir 1.5.9 for the GetDetailsOfElements
# `fields` filter and `hotlinkId` (a parameter, not a command, so no entry here
# could express it), and each Modify command per element type it is sent.
TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    "move_elements": ("MoveElements",),
    "delete_elements": ("DeleteElements",),
    "reserve_elements": ("ReserveElements",),
    "release_elements": ("ReleaseElements",),
    "list_issues": ("GetIssues",),
    "create_issue": ("CreateIssue",),
    "add_issue_comment": ("AddCommentToIssue",),
    "attach_elements_to_issue": ("AttachElementsToIssue",),
    "export_issues_bcf": ("ExportIssuesToBCF",),
    "import_issues_bcf": ("ImportIssuesFromBCF",),
    "publish": ("PublishPublisherSet",),
    "highlight_failures": ("HighlightElements",),
    "save_project": ("SaveProject",),
    "export_ifc": ("IFCFileOperation", "GetIFCExportTranslators"),
    "capture_window": ("SaveCurrentWindowAsImage", "GetCurrentWindowType"),
    "show_elements": ("ChangeWindow", "FitInWindow", "ChangeSelectionOfElements",
                      "HighlightElements", "GetNavigatorItemTree",
                      "GetDatabaseIdFromNavigatorItemId"),
    "create_issues_from_failures": ("CreateIssue", "AttachElementsToIssue"),
    "find_collisions": ("GetCollisions", "GetElementsByType", "GetDetailsOfElements"),
    "apply_favorites": ("GetFavoritesByType", "ApplyFavoritesToElements",
                        "GetDetailsOfElements"),
    "create_swept_beam": ("CreateObjects", "GetAvailableLibraryParts",
                          "SetGDLParametersOfElements"),
    "create_road_marking": ("CreateObjects", "GetAvailableLibraryParts",
                            "SetGDLParametersOfElements"),
    "edit_property_definitions": ("UpdateClassificationItems",),
    "edit_classifications": ("UpdateClassificationItems",),
    "import_definitions": ("UpdateClassificationItems",),
    # Each type's Create*/Get*, DeleteAttributes and the attribute property
    # commands are checked per change, in core/attribute_edit.py.
    "edit_attributes": ("GetAttributesByType",),
    "deploy_gdl_object": ("ReloadLibraries", "CreateObjects", "GetElementPreviewImage"),
}

# Commands the synced definitions do not carry yet. Remove an entry once a
# definitions sync brings the command in (a test fails when they disagree).
EXPLICIT_VERSIONS: dict[str, tuple[str, str]] = {}

# Commands whose definition names an older release than the one that first
# shipped them. A test fails once the definitions agree, so the entry can go.
_FIRST_IN_1_7_2 = ("new in the 1.7.2 definitions, labelled 1.7.1, absent from the 1.7.1 "
                   "release and from Tapir 1.7.1 on MCP-Test (21.10.2026)")
CORRECTED_VERSIONS: dict[str, tuple[str, str]] = {
    name: ("1.7.2", _FIRST_IN_1_7_2) for name in (
        "DeleteDesignOptionCombinations", "DeleteDesignOptionSets", "DeleteDesignOptions",
        "Get3DProjectionSettings", "RenameDesignOptionCombinations",
        "RenameDesignOptionSets", "RenameDesignOptions", "SaveCurrentWindowAsImage",
        "Set3DProjectionSettings")
}

_LOCAL = Path(__file__).parent / "gateway" / "definitions" / "local_commands.json"


def _local_names() -> frozenset[str]:
    data = json.loads(_LOCAL.read_text())
    return frozenset(c["name"] for g in data.get("groups", []) for c in g.get("commands", []))


# Commands only the local Tapir fork has: no release, so no minimum version.
FORK_ONLY: frozenset[str] = _local_names()


@lru_cache(maxsize=1)
def _registry_versions() -> dict[str, str | None]:
    from archicad_mcp.gateway.registry import build_registry
    return {name: info.version for name, info in build_registry().items()}


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def command_version(name: str) -> str | None:
    if name in FORK_ONLY:
        return None
    if name in EXPLICIT_VERSIONS:
        return EXPLICIT_VERSIONS[name][0]
    if name in CORRECTED_VERSIONS:
        return CORRECTED_VERSIONS[name][0]
    return _registry_versions().get(name)


def tool_minimum(tool: str) -> tuple[str | None, str]:
    """(version, the command that sets it); version None for a fork-only command."""
    commands = TOOL_COMMANDS[tool]
    for c in commands:
        if c in FORK_ONLY:
            return None, c
    newest = max(commands, key=lambda c: _key(command_version(c) or "0"))
    return command_version(newest), newest


def _installed_version(conn: ArchicadConnection) -> str | None:
    try:
        return conn.tapir("GetAddOnVersion").get("version")
    except Exception:
        return None


def _installed(conn: ArchicadConnection) -> str:
    version = _installed_version(conn)
    return f"Tapir {version}" if version else "a Tapir of unknown version"


def require_tapir_command(conn: ArchicadConnection, command: str, tool: str) -> dict | None:
    """None when this Archicad's Tapir has command, else an error result."""
    if not conn.tapir_available():
        return {"error": tapir_missing_message(conn, f"'{tool}'")}
    if conn.tapir_command_available(command):
        return None
    version = command_version(command)
    if version is None:
        return {"error": f"'{tool}' needs a Tapir build with {command}, which no Tapir "
                         f"release has yet; this Archicad's Tapir does not have it."}
    installed = _installed_version(conn)
    if installed and _key(installed) >= _key(version):
        # Tapir registers some commands only on some Archicad versions (the
        # electrical ones of 1.7.2 on Archicad 30 alone): a newer Tapir won't help.
        return {"error": f"'{tool}' needs {command}, which Tapir {installed} does not "
                         f"offer on Archicad {conn.archicad_version()}, although Tapir "
                         f"{version} introduced it: Tapir has it only on some Archicad "
                         "versions (its description says which). Nothing was sent."}
    from archicad_mcp.tapir_install.hint import install_hint
    return {"error": f"'{tool}' needs Tapir {version} or newer (this Archicad has "
                     f"{_installed(conn)}): it lacks {command}. Nothing was sent. "
                     + install_hint(conn.archicad_version(), replace=True)}


def tapir_refusal(conn: ArchicadConnection, tool: str) -> dict | None:
    """None when the tool can run, else the error result to return instead."""
    _, command = tool_minimum(tool)
    return require_tapir_command(conn, command, tool)


def unavailable_tools(conn: ArchicadConnection, reported: str | None = None) -> list[str]:
    """Gated tools whose minimum command this Tapir lacks.

    Only commands newer than the reported version are probed: an add-on that
    says 1.7.1 has every command released by then, and a custom build keeps
    the commands of the release it is based on. The version is asked for when
    not given; without one, probe all.
    """
    if not conn.tapir_available():
        return []
    if not reported:
        try:
            reported = conn.tapir("GetAddOnVersion").get("version")
        except Exception:
            reported = None
    floor = _key(reported) if reported else None
    missing = []
    for tool in sorted(TOOL_COMMANDS):
        version, command = tool_minimum(tool)
        if floor is not None and version is not None and _key(version) <= floor:
            continue
        if not conn.tapir_command_available(command):
            missing.append(tool)
    return missing
