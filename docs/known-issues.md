# Known issues

What is broken, what is unproven, and what has actually been verified against a
live Archicad. Everything here was found on **Archicad 29.0 build 4006** with
**Tapir 1.5.3** unless stated otherwise.

## Reading property values can crash Archicad

**This is the big one.** `GetPropertyValuesOfElements` crashed Archicad 29.0
build 4006 three times during development, with a `ComposeResult` abort that
takes unsaved work down with it.

It is **not** a volume problem. The third crash was a **single property on a
single element**: a user-defined property read on a freshly created slab.
Reading built-in properties (e.g. `ModelView_LayerName`) across thousands of real
elements worked fine. So the trigger appears to be *a specific
property/element combination*, most likely a property that is not applicable to
that element, which the API aborts on instead of returning a per-element error.

This is an Archicad-side fault. The server can trigger it but **cannot prevent
it**. Treat any `audit_delivery_readiness`, `run_rule`, `get_element_data`, or
`set_element_data` against a model you care about as capable of crashing it.
**Save first.**

### The element ceiling is blast-radius control, not a fix

The server refuses a property fetch spanning more than
`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` elements (default `5000`). This limits how
much work a crash can destroy. It does **not** make property reads safe. A
single-element read already crashed once.

Consequences:

- **`get_model_summary`** returns `by_type` only by default, which is cheap and
  safe. Pass `include_layer_story=true` for the per-layer/per-story breakdown;
  that reads a property across every element and is refused on very large models.
- **`audit_delivery_readiness`** scopes its property fetch to the element types
  its rules target (each rule's `applies_to`), so a typed audit (fire ratings on
  walls, say) reads only walls. An audit is refused only when a rule targets
  *all* elements and the model exceeds the ceiling. Scope the rule, or raise the
  env var.

## Element coverage: the official API sees only model elements

The official `API.GetAllElements` returns **model elements only**. Everything 2D
(markers, labels, dimensions, section lines, viewpoints) is missing from it.
Measured on one live project, same instance, same moment:

| Command | Elements returned |
|---|---|
| official `API.GetAllElements` | 16221 |
| Tapir `GetAllElements` | 63122 |

So the official command saw 26% of the project. `query_elements` and
`get_model_summary` used to be built on it and answered `count: 0` for types
that existed in the hundreds. A silent `0` is the worst possible output there:
it reads as "verified absent" and gets acted on.

Enumeration now goes through Tapir (`GetAllElements`, `GetElementsByType`,
`GetSelectedElements`) and falls back to the official command only when the
add-on is missing. Both tools report which one they got:

- `coverage: "whole-plan"` with Tapir.
- `coverage: "model-elements-only"` plus a `coverage_note` without it. In that
  state `element_count` is **not** a project total and a `count` of 0 is not
  proof of absence.

Asking for a single type is now one Tapir request instead of enumerating the
plan and reading back every element's type, so a typed query no longer costs
16k+ property reads.

**Still marker-blind:** `get_selection` reads the selection with the official
`API.GetSelectedElements`, which returns `[]` when a marker (a CutPlane, say) is
selected. `query_elements(selection_only=true)` does not have this problem.

## Teamwork credentials are stripped from `get_project_info`

On a Teamwork project, Tapir's `GetProjectInfo.projectLocation` is a
`teamwork://user:<JWT refresh token>@host/path` URL. The token is a live
credential, and returning it verbatim put it in the model's context and in the
session transcript. The tool now drops the `user:token` segment and keeps the
host and project path, with a regex backstop that redacts any JWT-shaped string
surviving in another field.

## The gateway tools tolerate `params` sent as text

Some MCP clients collapse a nullable object field (`dict | None`) to an untyped
schema and then send the value as a JSON string, which made every parameterized
command unreachable with `Input should be a valid dictionary`. The server emits
a correct schema, so this is a client-side defect, but `params` is now accepted
as either an object or a JSON-encoded object. The parsed value still goes
through schema validation. A string that is not JSON gets an explicit error and
no command is sent.

## Writing enum properties is not supported

`singleEnum` and `multiEnum` properties need an `EnumValueId`, not a plain value.
`set_element_data` reports them as `skipped` with a reason rather than silently
failing. To set one, use `execute_write_api_command` with the enum's id.

## Tapir version matters

IFC commands (`GetIFCPropertiesOfElements`) only exist in newer Tapir releases.
**1.4.0 does not have them** and IFC rules skip. Verified working on **1.5.3**.

The server probes availability per command, so an older add-on degrades to
`skipped` instead of erroring mid-fetch. Note that `project_name` and
`tapir_version` in `list_instances` come from Tapir too. Without the add-on they
are `null` even though the instance is otherwise fine.

## `publish` is unvalidated

The test model had no publisher sets, so `publish` has never run end to end.
Everything else in the tool list has, as listed below.

## Connection

The server scans ports **19723-19743** and picks the instance with a project
open. Behaviour worth knowing:

| Situation | What happens |
|---|---|
| Nothing on any port | `No running Archicad found. Start Archicad 29 and open a project.` |
| Archicad running, no project open | Reported by `list_instances` with `project_open: false` and version/build `0`. Archicad refuses even `API.GetProductInfo` (error `4001`) until a project is open, and tools then fail with a message naming the two possible causes below. |
| Archicad running, project open, but a modal dialog open (e.g. Object Settings) | The dialog blocks the whole API with the same error `4001` as a missing project, so this looks identical to the row above and the two cannot be told apart. Tools fail with a message naming both causes; close the dialog and retry. |
| Several instances, projects open | Discovery refuses to guess. Pass `port` to the tool; `list_instances` shows the options. |
| Archicad quits or crashes mid-session | Tools report that Archicad is not responding on that port and may have been closed or crashed. |

Tapir absence is concluded **only** from a command-level failure, never from a
transport failure. Otherwise a crashed Archicad would report as "Tapir not
installed" and send you off reinstalling a perfectly good add-on.

## Built-in property names (verified)

Confirmed against a live Archicad 29.0 model on 2026-07-16:

- The layer name is **`ModelView_LayerName`**. There is no `General_LayerName`.
- Zone number and name are **`Zone_ZoneNumber`** and **`Zone_ZoneName`**.
- An element's **story is not a property**. It comes from Tapir
  `GetDetailsOfElements.floorIndex`, a **0-based index**, which is why
  `query_elements(story=…)` and the `get_model_summary` breakdowns key on
  floorIndex.

## Validated end to end

Live-run against Archicad 29.0/4006:

- Instance discovery, `get_model_summary`, `query_elements` (type + story filters)
- `get_element_data`, `set_element_data` (dry-run, commit, read-back)
- `get_selection`, `set_selection` (replaces rather than appends), `clear_selection`
- `create_elements`, then `move_elements`, then `delete_elements`, including
  their dry-run and `confirm=true` guards
- `audit_delivery_readiness`, `run_rule`, `highlight_failures`,
  `create_issues_from_failures`, `verify_ifc_export_readiness`, `list_issues`,
  `create_issue`
- The tier-3 gateway (309 commands, writes included)

The 0.2.0 `win32` bundle, cross-built on macOS, was installed on Windows and
runs: the extension starts, the tools appear, and it reaches Archicad. That is
the check the build machine cannot perform for itself.

The live runs above predate the 0.2.0 tool split, which renamed these tools
without changing what they send to Archicad: `manage_selection` became
`get_selection` / `set_selection` / `clear_selection`, `manage_issues` became
`list_issues` and five single-purpose write tools, and `execute_api_command`
became `execute_read_api_command` and `execute_write_api_command`. The offline
suite covers the new surface; the live re-run is still owed.

Not validated: `publish`.

Not yet re-verified live: the Tapir-backed enumeration, the `coverage` field and
the `projectLocation` scrub above. They are covered by unit tests against
recorded API shapes, not by a live run.

## Running the live canary

Read-only checks against a running instance. Use a **small, non-sensitive**
model, never a client or teamwork project, and re-read the crash warning above:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

Pinning the port is deliberate: it stops the suite finding and touching whatever
model happens to be open.

## Schedules

Schedules have no programmatic interface. No command in the official JSON API
or Tapir reads or writes a schedule, and Graphisoft's developer forum states
the C++ API does not reach them either. The only supported route is the Scheme
Settings Import and Export XML, which is what the `*_schedule_scheme` tools
operate on. This means every schedule edit needs two manual clicks in Archicad,
before and after.

Whether re-importing an edited scheme updates it in place or creates a numbered
duplicate is **not yet confirmed**. Graphisoft's documentation says duplicate
names are auto-numbered, but exported schemes carry stable IDs that suggest an
in-place match may be possible. Test on a scratch project before relying on
either behaviour.
