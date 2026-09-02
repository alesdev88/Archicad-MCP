from __future__ import annotations

import json
import re
import typing
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from multiconn_archicad.core.literal_commands import AddonCommandType

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
LOCAL_DEFINITIONS = DEFINITIONS_DIR / "local_commands.json"
OFFICIAL_DOCS = "https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/"
TAPIR_DOCS = "https://github.com/ENZYME-APD/tapir-archicad-automation"

# Read verbs, anchored so that a prefix only matches a whole leading word: "Get"
# and "IsAlive" are reads, a hypothetical "Issue..." would not be caught by "Is".
_READ_VERB = re.compile(r"^(?:Get|Is)(?=[A-Z]|$)")

# The reads whose names do not begin with a read verb. FilterElements is handed a
# list of GUIDs and returns the subset matching a filter: it inspects, it does not
# act. Kept as an explicit set rather than more regex, because every entry here is
# a judgement about one command and should have to be argued for individually.
_READ_COMMANDS = frozenset({"FilterElements"})


def classify_access(name: str) -> str:
    """Return "read" or "write" for one API command name.

    Unrecognised means write. That is the direction that fails safe: a write
    misfiled as a read would run through the read tool, which is marked
    readOnlyHint and therefore runs without the confirmation prompt a destructive
    tool gets, while a read misfiled as a write costs one prompt nobody needed.
    The gateway reaches commands like DeleteElements and QuitArchicad, so the
    asymmetry between those two mistakes is not close.
    """
    bare = name.split(".")[-1]
    if bare in _READ_COMMANDS:
        return "read"
    return "read" if _READ_VERB.match(bare) else "write"


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
    # "read" or "write", from classify_access. Decides which of the two gateway
    # tools will run this command, and nothing else reads it.
    access: str = "write"

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
                version=cmd.get("version"), access=classify_access(cmd["name"]))

    # Commands that exist only in the local Tapir fork. They are merged here so
    # that every route into the add-on consults one registry: without this,
    # create_elements would reach a fork command that execute_write_api_command
    # refuses by name. Upstream definitions win on a name clash, because a
    # command that has landed upstream no longer needs the overlay.
    if LOCAL_DEFINITIONS.exists():
        local = json.loads(LOCAL_DEFINITIONS.read_text(encoding="utf-8"))
        for group in local.get("groups", []):
            for cmd in group.get("commands", []):
                if cmd["name"] in registry:
                    continue
                schema = cmd.get("inputScheme")
                resolved = _resolve_refs(schema, definitions) if schema is not None else None
                registry[cmd["name"]] = CommandInfo(
                    name=cmd["name"], kind="tapir", group=group["name"],
                    description=cmd.get("description", ""), input_schema=resolved,
                    version=cmd.get("version"), access=classify_access(cmd["name"]))

    for name in typing.get_args(AddonCommandType):
        if name in registry:
            continue
        registry[name] = CommandInfo(
            name=name, kind="official", group="Official JSON API",
            description=f"Official Archicad JSON API command. Docs: {OFFICIAL_DOCS}",
            input_schema=None, access=classify_access(name))

    return registry
