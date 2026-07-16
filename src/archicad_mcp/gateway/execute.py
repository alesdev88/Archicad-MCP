from __future__ import annotations

import difflib

import jsonschema

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import CommandInfo


def list_api_commands(registry: dict[str, CommandInfo], group: str | None = None) -> dict:
    commands = [c for c in registry.values() if group is None or c.group == group]
    return {
        "groups": sorted({c.group for c in registry.values()}),
        "commands": [{"name": c.name, "kind": c.kind, "group": c.group,
                      "summary": c.description.split(".")[0][:120]}
                     for c in sorted(commands, key=lambda c: (c.group, c.name))],
    }


def describe_api_command(registry: dict[str, CommandInfo], name: str) -> dict:
    info = registry.get(name)
    if info is None:
        close = difflib.get_close_matches(name, registry.keys(), n=3)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return {"error": f"Unknown command '{name}'.{hint} "
                         "Use list_api_commands to browse."}
    return info.to_dict()


def execute_api_command(registry: dict[str, CommandInfo], conn: ArchicadConnection,
                        name: str, params: dict | None = None) -> dict:
    info = registry.get(name)
    if info is None:
        return describe_api_command(registry, name)  # carries the error + suggestions
    if info.kind == "tapir" and info.input_schema is not None:
        try:
            jsonschema.validate(params or {}, info.input_schema)
        except jsonschema.ValidationError as exc:
            return {"error": f"Parameters for '{name}' failed validation: {exc.message}. "
                             "If the live add-on disagrees, refresh definitions with "
                             "scripts/sync_tapir_defs.py.",
                    "schema": info.input_schema}
    if info.kind == "official":
        return conn.official(name, params)
    return conn.tapir(name, params)
