"""run_script and apply_changeset.

Registered only when scripts are enabled (--enable-scripts or
ARCHICAD_MCP_SCRIPTS) in full mode. There is no sandbox: the switch, and the
refusal to serve scripts over http without a second flag, are the boundary.
"""
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection, get_connection, probe_port
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting import child
from archicad_mcp.scripting.apply import apply_changeset as _apply
from archicad_mcp.scripting.changesets import SAMPLE, ChangesetStore, summarize
from archicad_mcp.scripting.execute import cap_result, cap_text

MAX_TIMEOUT_S = 600.0

# Module-level so tests can replace it; apply opens its own connection because
# the changeset, not the caller, names the port.
open_connection = ArchicadConnection

RUN_DESCRIPTION = (
    "Run a short Python script next to Archicad and return only what the script "
    "returns. Use it for tasks spanning many elements, so GUID lists and raw "
    "element data never pass through the conversation. The script sees `ac`: "
    "ac.find(groups) -> GUIDs (same groups as find_elements), "
    "ac.props(guids, ['Group/Name', ...]) -> {guid: {name: value}}, "
    "ac.details(guids) -> {guid: Tapir element details}, "
    "ac.cmd(name, params) for any API command, "
    "ac.set_props([(guid, 'Group/Name', value), ...]); errors raise "
    "ac.ArchicadError. Set `result` to what you want back; print() is captured "
    "too; both are capped at max_output_chars. NEVER WRITES: set_props and "
    "write commands are recorded into a changeset, returned as counts, 20 sample "
    "changes (current -> new) and skipped changes with reasons. Apply it with "
    "apply_changeset. Runs in a separate process, stopped after timeout_s "
    "(max 600).")

APPLY_DESCRIPTION = (
    "Apply a changeset planned by run_script: writes exactly what was previewed, "
    "in order, then reads every written property back. Refuses without "
    "confirm=true, and refuses if a different project is now open on the port. "
    "Single use; changesets expire after 30 minutes. Returns applied, failed "
    "(guid, property, code, message per refused element), mismatched readbacks, "
    "and command outcomes; a failed command stops the run.")


def execute_script(store: ChangesetStore, code: str, port: int | None,
                   timeout_s: float, max_output_chars: int) -> dict:
    # Resolve the port the way every tool does, so "several instances, pass
    # port" is answered before a child process starts.
    conn = get_connection(port)
    info = probe_port(conn.port)
    project = info.project_name if info is not None else None
    timeout = min(max(float(timeout_s), 1.0), MAX_TIMEOUT_S)
    limit = max(int(max_output_chars), 1)
    reply = child.run_child(code, conn.port, timeout)

    out: dict = {}
    truncated: list[str] = []
    if "result" in reply:
        out["result"], cut = cap_result(reply["result"], limit)
        if cut:
            truncated.append("result")
    if reply.get("stdout"):
        out["stdout"], cut = cap_text(reply["stdout"], limit)
        if cut:
            truncated.append("stdout")
    if reply.get("error"):
        out["error"] = reply["error"]
        for key in ("traceback", "stderr"):
            if reply.get(key):
                out[key] = reply[key]
    elif reply.get("operations"):
        cs = store.add(conn.port, project, reply["operations"], reply.get("skipped", []))
        out["changeset"] = summarize(cs)
    elif reply.get("skipped"):
        # Nothing to apply, but the caller has to learn why its writes vanished.
        out["skipped"] = len(reply["skipped"])
        out["skipped_sample"] = reply["skipped"][:SAMPLE]
    if truncated:
        out["truncated"] = truncated
    return out


def register(mcp, default_port, tool_meta, guarded) -> None:
    store = ChangesetStore()
    registry = build_registry()

    @mcp.tool(description=RUN_DESCRIPTION,
              **tool_meta("Run a script", read_only=False, destructive=True))
    @guarded
    def run_script(code: str, port: int | None = None, timeout_s: float = 120,
                   max_output_chars: int = 20000) -> dict:
        return execute_script(store, code, port if port is not None else default_port,
                              timeout_s, max_output_chars)

    @mcp.tool(description=APPLY_DESCRIPTION,
              **tool_meta("Apply a changeset", read_only=False, destructive=True))
    @guarded
    def apply_changeset(changeset_id: str, confirm: bool = False) -> dict:
        # Looked up at call time, so tests can replace them on this module.
        return _apply(store, changeset_id, confirm, registry,
                      probe=probe_port, connect=open_connection)
