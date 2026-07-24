from __future__ import annotations

import json
import typing
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from multiconn_archicad.core.literal_commands import AddonCommandType

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
OFFICIAL_DOCS = "https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/"


@dataclass(frozen=True)
class CommandInfo:
    name: str
    kind: str
    group: str
    description: str
    input_schema: dict | None
    # The Tapir add-on version a command was first included in (Tapir stamps each
    # command with a "since" version). None for official API commands.
    version: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _load_js_json(path: Path, var_name: str):
    text = path.read_text(encoding="utf-8")
    text = text.replace(f"var {var_name} = ", "").rstrip("; \n")
    return json.loads(text)


def _resolve_refs(schema, definitions, seen=None):
    # `seen` tracks the refs on the CURRENT path (immutable, per-branch) so that
    # diamond references (same def reached via two sibling branches) resolve fully,
    # while a genuine cycle (e.g. the self-recursive ClassificationItemDetails) is
    # truncated to an unconstrained schema rather than leaking an unresolved "$ref".
    if seen is None:
        seen = frozenset()
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref.startswith("#/"):
                key = ref[2:]
                if key in seen:
                    return {}
                return _resolve_refs(definitions[key], definitions, seen | {key})
        return {k: _resolve_refs(v, definitions, seen) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_resolve_refs(item, definitions, seen) for item in schema]
    return schema


@lru_cache(maxsize=1)
def build_registry() -> dict[str, CommandInfo]:
    registry: dict[str, CommandInfo] = {}

    groups = _load_js_json(DEFINITIONS_DIR / "command_definitions.js", "gCommands")
    definitions = _load_js_json(
        DEFINITIONS_DIR / "common_schema_definitions.js", "gSchemaDefinitions")
    for group in groups:
        for cmd in group.get("commands", []):
            schema = cmd.get("inputScheme")
            resolved = _resolve_refs(schema, definitions) if schema is not None else None
            registry[cmd["name"]] = CommandInfo(
                name=cmd["name"], kind="tapir", group=group["name"],
                description=cmd.get("description", ""), input_schema=resolved,
                version=cmd.get("version"))

    for name in typing.get_args(AddonCommandType):
        if name in registry:
            continue
        registry[name] = CommandInfo(
            name=name, kind="official", group="Official JSON API",
            description=f"Official Archicad JSON API command. Docs: {OFFICIAL_DOCS}",
            input_schema=None)

    return registry
