from __future__ import annotations

import difflib
import json

import jsonschema

from collections.abc import Callable

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import OFFICIAL_DOCS, TAPIR_DOCS, CommandInfo


def list_api_commands(registry: dict[str, CommandInfo], group: str | None = None,
                      access: str | None = None) -> dict:
    commands = [c for c in registry.values()
                if (group is None or c.group == group)
                and (access is None or c.access == access)]
    return {
        "groups": sorted({c.group for c in registry.values()}),
        "docs": {"official": OFFICIAL_DOCS, "tapir": TAPIR_DOCS},
        # Which of the two execute tools each command belongs to, in the listing
        # rather than only in describe_api_command, so that picking a command and
        # picking the tool that runs it is one decision instead of two round trips.
        "commands": [{"name": c.name, "kind": c.kind, "group": c.group,
                      "access": c.access,
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


def _validate(info: CommandInfo, params: dict | None) -> dict | None:
    """Schema-check params for a Tapir command. Returns an error dict, or None."""
    if info.kind != "tapir" or info.input_schema is None:
        return None
    try:
        jsonschema.validate(params or {}, info.input_schema)
    except jsonschema.ValidationError as exc:
        return {"error": f"Parameters for '{info.name}' failed validation: {exc.message}. "
                         "If the live add-on disagrees, refresh definitions with "
                         "scripts/sync_tapir_defs.py.",
                "schema": info.input_schema}
    return None


def _dispatch(conn: ArchicadConnection, info: CommandInfo, params: dict | None) -> dict:
    if info.kind == "official":
        return conn.official(info.name, params)
    return conn.tapir(info.name, params)


def _resolve(registry: dict[str, CommandInfo], name: str,
             params: dict | str | None) -> tuple[CommandInfo | None, dict | None, dict | None]:
    """Shared prologue: coerce params, look the command up. Returns (info, params, error)."""
    params, param_error = _coerce_params(params)
    if param_error is not None:
        return None, None, {"error": param_error}
    info = registry.get(name)
    if info is None:
        # Carries the "unknown command" error plus close-match suggestions.
        return None, None, describe_api_command(registry, name)
    return info, params, None


# Both entry points take a factory rather than a connection, so that nothing
# below opens one until a command has cleared every gate. It matters because
# connecting can fail on its own terms (no Archicad running, several running and
# no port chosen), and an eagerly-opened connection makes that failure the only
# thing the caller ever hears. Asking to run DeleteElements through the read
# tool should say so, not report that three copies of Archicad are open.
Connect = Callable[[], ArchicadConnection]


def execute_read_api_command(registry: dict[str, CommandInfo], connect: Connect,
                             name: str, params: dict | str | None = None) -> dict:
    """Run a command classified read. Refuses anything that can change the model.

    The refusal is the point of the split rather than a side effect of it. This
    tool is annotated readOnlyHint, which is what lets a client run it without
    asking the user first, so it has to be unable to reach a write no matter what
    name it is handed.
    """
    info, params, error = _resolve(registry, name, params)
    if error is not None:
        return error
    if info.access != "read":
        return {"error": f"'{name}' modifies the project, so it cannot run here. "
                         "Use execute_write_api_command, which asks for confirmation.",
                "access": info.access}
    schema_error = _validate(info, params)
    return schema_error if schema_error is not None else _dispatch(connect(), info, params)


def execute_write_api_command(registry: dict[str, CommandInfo], connect: Connect,
                              name: str, params: dict | str | None = None,
                              confirm: bool = False) -> dict:
    """Run a command classified write. Refuses without confirm=true.

    Same gate as move_elements and delete_elements, and for a stronger reason:
    this one reaches every write command Archicad and Tapir expose, including
    DeleteElements and QuitArchicad, and the caller chose the name at runtime.
    """
    info, params, error = _resolve(registry, name, params)
    if error is not None:
        return error
    if info.access == "read":
        return {"error": f"'{name}' only reads. Use execute_read_api_command, "
                         "which runs without a confirmation prompt.",
                "access": info.access}
    if not confirm:
        return {"error": f"'{name}' changes the project and was not confirmed. "
                         "Re-send with confirm=true to run it.",
                "command": name, "params": params or {}}
    schema_error = _validate(info, params)
    return schema_error if schema_error is not None else _dispatch(connect(), info, params)
