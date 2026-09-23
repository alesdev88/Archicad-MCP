# run_script: Bulk Operations Without GUID Round Trips: Design

**Date:** 2026-09-23
**Status:** Approved in brainstorming, not yet implemented.
**Repo:** https://github.com/alesdev88/Archicad-MCP.git
**Source:** handoff `handoff-bulk-script-tool-260923.md` in the Second Brain vault
(`20_projects/_active/archicad-mcp/`), section 3.1.

## Purpose

Let an MCP client do an N-element task in two calls regardless of N, by running
a short Python script next to Archicad and returning only what the script
chooses to return.

## What forced this

Numbering the 403 doors of the CVP Teamwork model gave the right result but took
a long session, while Archicad itself spent seconds. Every bulk tool takes an
explicit GUID list and returns every result into the model's context, so the
data had to travel through the model: about eight full GUID lists typed by the
model and several hundred kB of results read back. That volume scales linearly
with element count; a 3000-element task would be unworkable.

A script that runs inside the server's machine keeps the raw element data local.
It also fits the project's verdicts-only principle better than the current
tools, because the model sees a summary rather than the model data.

## Decisions taken

| Question | Decision |
|---|---|
| How is it switched on | Opt-in flag, off by default, full mode only. Refused under `--transport http` unless a second flag allows it. |
| How preview and write connect | A stored changeset. `run_script` only plans; `apply_changeset` writes exactly what was previewed. Nothing is re-executed. |
| Where the script runs | A child process per call, killed at the timeout. |
| What a changeset may hold | Property writes and write-classified API commands, in recorded order. |

Dropped from the handoff sketch: the restricted-builtins sandbox. A Python
sandbox built that way can be escaped, and presenting it as a boundary would
claim protection that does not exist. The boundary is who can reach the tool:
the opt-in flag and the http refusal.

## Tools

Both register only when scripts are enabled (see Configuration).

### `run_script(code, port=None, timeout_s=120, max_output_chars=20000)`

Runs `code` in a child process with a ready `ac` object. Never writes to the
model: every write the script asks for is recorded into a changeset.

Annotated `readOnlyHint: false, destructiveHint: true`. The script is ordinary
Python and could reach Archicad or the filesystem through its own imports, so a
read-only hint, which lets a client run a tool without asking, would be false.

`timeout_s` is clamped to 1..600. `max_output_chars` caps `result` and `stdout`
separately.

Returns:

```json
{
  "result": "... the script's `result` variable, JSON, capped ...",
  "stdout": "... captured print output, capped ...",
  "truncated": ["stdout"],
  "changeset": {
    "id": "cs-7f3a...",
    "expires_at": "2026-09-23T14:05:00Z",
    "port": 19724,
    "project": "Oprema-objekti",
    "property_writes": 401,
    "commands": {"API.SetClassificationsOfElements": 1},
    "skipped": 2,
    "sample": [{"guid": "...", "property": "ELEA - Vrata/Pozicija",
                "current": "0", "new": "001"}],
    "skipped_sample": [{"guid": "...", "property": "...", "reason": "..."}]
  }
}
```

`changeset` is absent when the script recorded no writes. `sample` and
`skipped_sample` hold at most 20 entries each. `truncated` lists the fields the
cap cut; each cut field also ends with a visible marker
(`... [truncated, N more characters]`).

### `apply_changeset(changeset_id, confirm=False)`

Writes a stored changeset. Takes no `port`: the changeset carries the port and
the project name it was planned against.

Annotated `readOnlyHint: false, destructiveHint: true`.

Steps:

1. Look up the changeset. Unknown, expired or already applied: error.
2. Without `confirm=true`: error, and repeat the changeset summary.
3. Probe the port. If the open project's name differs from the one recorded:
   error, nothing written.
4. Run the recorded operations in recorded order. Consecutive property writes
   are grouped and sent through `API.SetPropertyValuesOfElements` in batches of
   `PROPERTY_FETCH_CHUNK`. Recorded commands go through the same dispatch as
   `execute_write_api_command`.
5. Read back every written property and compare with the value sent.
6. Mark the changeset applied (single use), whatever the outcome.

Returns:

```json
{
  "applied": 399,
  "failed": [{"guid": "...", "property": "...", "code": 6001,
              "message": "TeamWork permission denied"}],
  "mismatched": [{"guid": "...", "property": "...", "sent": "001", "read": "1"}],
  "commands": [{"name": "API.SetClassificationsOfElements", "ok": true}]
}
```

`failed` has the same shape `set_element_data` returns since commit 2edd550.
`failed` and `mismatched` are capped at 50 entries with a count of the rest.

A failed recorded command stops the run: later operations may depend on it. The
response reports the failure and everything applied before it. Property batches
do not stop the run; their failures are per element.

## The `ac` object

Available in the script as a global. Version 1:

| Call | Returns | Built on |
|---|---|---|
| `ac.find(groups, selection_only=False)` | list of GUIDs | `core.query.find_elements`, same group schema as `find_elements` |
| `ac.props(guids, names)` | `{guid: {name: value}}` | `extract.fetch_property_values` |
| `ac.details(guids)` | `{guid: details}` | Tapir `GetDetailsOfElements`, chunked |
| `ac.cmd(name, params=None)` | reads: the response; writes: `{"recorded": n}` | gateway registry, `classify_access`, schema validation |
| `ac.set_props(changes)` | `{"planned": n, "skipped": m}` | the planning half of `set_element_data` |
| `ac.port` | the port the script is connected to | |

`changes` for `set_props` is a list of `(guid, property, value)` tuples or
`{"guid", "property", "value"}` dicts.

Property reads keep the existing element ceiling
(`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS`, default 5000). The ceiling exists because
wide reads have crashed Archicad, and running inside a script does not change
that. A script that needs more reads in scoped chunks.

An API refusal inside any `ac` call raises `ac.ArchicadError` with `code` and
`message`, so a script can catch it.

`ac.cmd` refuses an unknown command name the same way the gateway does. It
never sends a write-classified command; it records it, validated against the
schema first so a bad payload fails in the dry run, not in the apply.

### Planning checks in `set_props`

Each change is checked when planned, reading the element's current cell:

| Condition | Result |
|---|---|
| property name does not resolve | skipped, reason given |
| no value type (property not available on the element) | skipped |
| `singleEnum` / `multiEnum` | skipped, reason points to `ac.cmd` with the enum id |
| value does not fit the type | skipped, reason names both |
| otherwise | planned: finished API payload plus the current value |

"Fits the type": `string` takes a `str`; `integer` takes an `int`, or a `str`
whose `int()` round-trips exactly (so `"12"` fits and `"001"` does not);
`number` takes an `int` or `float`; `boolean` takes a `bool`. Other types pass
through unchecked and are left to Archicad, with any refusal reported per element
at apply time.

This is the Integer vs `"001"` case from the CVP run, caught in the dry run.

`core/element_data.set_element_data` is split into `plan_property_writes`
(cells, ids, checks, payloads, skipped) and `send_property_writes` (batched
send, per-element `failed`). `set_element_data` keeps its current behaviour on
top of the two, and both tools share one tested path.

## Execution

New package `src/archicad_mcp/scripting/`:

| Module | Job |
|---|---|
| `api.py` | the `ac` class and `ArchicadError`; a `Recorder` that holds planned operations |
| `execute.py` | `execute(code, ac) -> Outcome`: runs code with `ac` and `result` bound, captures stdout and traceback. No process handling, so it is testable against `FakeCore`. |
| `runner.py` | child entry point (`python -I -m archicad_mcp.scripting.runner`): reads `{code, port}` JSON on stdin, builds a real connection and `ac`, calls `execute`, writes one JSON document on stdout |
| `child.py` | the server side: spawns the runner with `sys.executable`, enforces the timeout, parses the reply |
| `changesets.py` | in-memory store: id, port, project, operations, created time; 30-minute expiry, at most 20 kept (oldest evicted), single use |
| `tools.py` | `register(mcp, default_port, tool_meta, guarded)`, mirroring `gdl/tools.py` |

Flow of `run_script`:

1. Resolve the port with `get_connection`, as every tool does. This also
   reports "several instances, pass port" before any child starts.
2. Read the project name from `probe_port` for the changeset.
3. Spawn the runner with `sys.executable -I` so the bundled interpreter is used
   and no user `PYTHONPATH` or user `site-packages` leaks in. Send `{code, port}`
   on stdin.
4. Wait up to `timeout_s`. On timeout kill the child.
5. Parse the reply, store the operations as a changeset if any, cap outputs.

Stdout of the child carries only the reply document. The script's own `print`
output is captured by `execute` into a buffer, so a stray print cannot corrupt
the protocol.

`result` values that are not JSON serialisable are converted with `str()`.

## Configuration

| Surface | Name |
|---|---|
| CLI | `--enable-scripts` |
| environment | `ARCHICAD_MCP_SCRIPTS` (`1`, `true`, `yes`, case-insensitive) |
| bundle (`manifest.json` `user_config`) | `enable_scripts`, boolean, default false, mapped to the env var |
| http override | `--allow-scripts-over-http` (CLI only, on purpose) |

Rules:

- Registered only when enabled and `mode == "full"`. Enabled in verdicts mode:
  ignored, and the banner says `scripts ignored in verdicts mode`.
- Enabled with `--transport http` and no override: the server exits at startup
  with a message saying why. Someone who asked for scripts must learn they are
  not getting them.
- The banner gains `scripts on` when the tools are registered.
- `manifest.json` lists both tools, as it lists the conditional GDL tools.

## Errors

`run_script`:

| Case | Response |
|---|---|
| no Archicad, several instances, no project | the usual `get_connection` error; no child started |
| timeout | `error: "script timed out after N s"`, stdout so far; no changeset |
| exception in the script | `error`, last 30 traceback lines, stdout; no changeset |
| child exits without a valid reply | `error` plus the last 2000 characters of its stderr |

A script that raises discards its recorded operations: a half-planned changeset
is not offered for apply.

`apply_changeset`: see the Tools section.

## Testing

In-process, against `FakeCore`:

- a dry run sends no write-classified command (checked on `core.calls`)
- `ac.cmd` with a write command records it, sends nothing, validates the schema
- `ac.cmd` with an unknown name raises with close matches
- `set_props` planning: unresolved property, not available, enum, `"001"` into
  an integer, a valid string write
- an API refusal surfaces as `ac.ArchicadError` with code and message
- output cap and marker on `result` and `stdout`
- a script exception discards the changeset

Child process, with scripts that do not touch Archicad:

- `result = 1` round trip
- an infinite loop is killed at the timeout
- `sys.exit(3)` and a hard crash are reported with stderr
- a large `print` is capped and does not corrupt the reply

`apply_changeset`:

- refuses without `confirm`; repeats the summary
- single use; unknown and expired ids
- refuses when the port's project name changed
- per-element `failed` and readback `mismatched`
- stops at the first failed recorded command and reports what ran

Registration and config:

- absent without the flag; absent in verdicts mode
- http without override exits; with override registers
- env var parsing; manifest lists both tools (`tests/test_manifest.py`)
- tool annotations (`tests/test_tool_annotations.py`)

Live acceptance (handoff section 5), on a scratch copy of CVP or on
Oprema-objekti (port 19724), never on the live CVP model:

- one `run_script` returns counts per storey and 20 sample changes
- one `apply_changeset(confirm=true)` writes all values and reports failures
- a follow-up `run_script` reads the values back and confirms uniqueness
- total wall time under 2 minutes, context traffic under about 10k characters

## Documentation

- `docs/scripting.md`: the `ac` reference, the plan and apply flow, the trust
  model (no sandbox; the flag is the boundary), and one worked example.
- README: a Scripting section and the two tools in the tool list.
- `docs/known-issues.md`: the property-read ceiling applies inside scripts.

## Out of scope for version 1

From the handoff, planned after this lands:

- `ac.door_window_location`, a `story` pseudo-property, a preflight helper
  (handoff 3.3.4, 3.3.5, 3.4)
- server-side element sets (`set_id`) across the existing tools (handoff 3.2)
- hotlink detection in `reserve_elements` (handoff 3.3.3)
- spilling large gateway results to a file (handoff 3.3.6)
- a live mode that lets a script write and then read its own results
