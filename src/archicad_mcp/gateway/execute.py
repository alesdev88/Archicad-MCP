from __future__ import annotations

import difflib
import json

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


def _coerce_params(params: dict | str | None) -> tuple[dict | None, str | None]:
    """Returns (params, error). Accepts a JSON-encoded object as well as a dict.

    A client that collapses this tool's nullable object field to an untyped
    schema sends the value as text, and a strict dict-only signature then makes
    every parameterized command unreachable. Parsing the string is a workaround
    for that client bug, not an invitation to pass text.
    """
    if not isinstance(params, str):
        return params, None
    text = params.strip()
    if not text:
        return None, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, (f"params arrived as text and is not valid JSON ({exc.msg}). "
                      "Pass an object, or a JSON-encoded object.")
    if not isinstance(parsed, dict):
        return None, (f"params must be a JSON object, got {type(parsed).__name__}. "
                      "Use describe_api_command to see the expected shape.")
    return parsed, None


def execute_api_command(registry: dict[str, CommandInfo], conn: ArchicadConnection,
                        name: str, params: dict | str | None = None) -> dict:
    params, param_error = _coerce_params(params)
    if param_error is not None:
        return {"error": param_error}
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
