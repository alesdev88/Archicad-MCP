"""The `ac` object a script sees.

Reads go straight to Archicad. Writes never do: property writes are planned
(checked and turned into finished payloads) and write-classified commands are
validated, and both are appended to a Recorder. The server turns the recording
into a changeset that apply_changeset sends later, so nothing a script does can
change the model before the user has seen the preview.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.element_data import plan_property_writes
from archicad_mcp.core.query import find_elements
from archicad_mcp.extract import (
    MAX_PROPERTY_FETCH_ELEMENTS,
    PROPERTY_FETCH_CHUNK,
    PropertyFetchTooWideError,
    element_payload,
    fetch_property_values,
)
from archicad_mcp.gateway.execute import _dispatch, _validate, describe_api_command
from archicad_mcp.gateway.registry import CommandInfo


class ArchicadError(Exception):
    """Archicad or Tapir refused a call made from a script. `code` may be None."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message if code is None else f"{message} (code {code})")
        self.message = message
        self.code = code


def _translated(method):
    # Scripts catch one exception type for every refusal, instead of learning
    # multiconn's error hierarchy.
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except APIErrorBase as exc:
            raise ArchicadError(exc.message, getattr(exc, "code", None)) from None
        except ArchicadUnavailableError as exc:
            raise ArchicadError(str(exc)) from None
    return wrapper


@dataclass
class Recorder:
    """Planned operations, in the order the script asked for them."""
    operations: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def add_writes(self, planned: list[dict]) -> None:
        if not planned:
            return
        # Consecutive set_props calls become one operation, so apply sends them
        # in as few batches as possible.
        if self.operations and self.operations[-1]["kind"] == "props":
            self.operations[-1]["writes"].extend(planned)
        else:
            self.operations.append({"kind": "props", "writes": list(planned)})

    def add_command(self, name: str, params: dict | None) -> int:
        self.operations.append({"kind": "command", "name": name, "params": params})
        return sum(1 for op in self.operations if op["kind"] == "command")


def _change(item) -> dict:
    if isinstance(item, dict):
        return {"guid": item["guid"], "property": item["property"],
                "value": item["value"]}
    guid, prop, value = item
    return {"guid": guid, "property": prop, "value": value}


class ScriptAPI:
    ArchicadError = ArchicadError

    def __init__(self, conn: ArchicadConnection, registry: dict[str, CommandInfo],
                 recorder: Recorder):
        self._conn = conn
        self._registry = registry
        self._recorder = recorder

    @property
    def port(self) -> int:
        return self._conn.port

    @_translated
    def find(self, groups: list[dict], selection_only: bool = False) -> list[str]:
        result = find_elements(self._conn, groups, selection_only)
        if "error" in result:
            raise ValueError(result["error"])
        return result["guids"]

    @_translated
    def props(self, guids, names) -> dict[str, dict[str, object]]:
        return fetch_property_values(self._conn, list(guids), list(names))

    @_translated
    def details(self, guids) -> dict[str, dict]:
        guids = list(guids)
        # Same ceiling as property reads: the element-count limit is about how
        # much one read asks of Archicad, and a script is no exception.
        if len(guids) > MAX_PROPERTY_FETCH_ELEMENTS:
            raise PropertyFetchTooWideError(
                f"Refusing to read element details across {len(guids)} elements "
                f"(limit {MAX_PROPERTY_FETCH_ELEMENTS}). Read in scoped chunks, or "
                "raise ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS if you accept the risk.")
        out: dict[str, dict] = {}
        for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
            chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
            response = self._conn.tapir("GetDetailsOfElements",
                                        {"elements": element_payload(chunk)})
            for guid, item in zip(chunk, response.get("detailsOfElements", [])):
                out[guid] = item
        return out

    @_translated
    def cmd(self, name: str, params: dict | None = None):
        info = self._registry.get(name)
        if info is None:
            raise ValueError(describe_api_command(self._registry, name)["error"])
        error = _validate(info, params)
        if error is not None:
            raise ValueError(error["error"])
        if info.access == "read":
            return _dispatch(self._conn, info, params)
        # Validated now, so a malformed payload fails in the preview rather
        # than halfway through apply_changeset.
        return {"recorded": self._recorder.add_command(name, params)}

    @_translated
    def set_props(self, changes) -> dict:
        planned, skipped = plan_property_writes(self._conn, [_change(c) for c in changes])
        self._recorder.add_writes(planned)
        self._recorder.skipped.extend(skipped)
        return {"planned": len(planned), "skipped": len(skipped)}
