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

## Writing enum properties is not supported

`singleEnum` and `multiEnum` properties need an `EnumValueId`, not a plain value.
`set_element_data` reports them as `skipped` with a reason rather than silently
failing. To set one, use `execute_api_command` with the enum's id.

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
| Archicad running, no project open | Reported by `list_instances` with `project_open: false` and version/build `0`. Archicad refuses even `API.GetProductInfo` (error `4001`) until a project is open, and tools then fail with a message saying exactly that. |
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
- `manage_selection` (`set` replaces the selection)
- `create_elements`, then `move_elements`, then `delete_elements`, including
  their dry-run and `confirm=true` guards
- `audit_delivery_readiness`, `run_rule`, `highlight_failures`,
  `create_issues_from_failures`, `verify_ifc_export_readiness`, `manage_issues`
- The tier-3 gateway (231 commands, writes included)

Not validated: `publish`.

## Running the live canary

Read-only checks against a running instance. Use a **small, non-sensitive**
model, never a client or teamwork project, and re-read the crash warning above:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

Pinning the port is deliberate: it stops the suite finding and touching whatever
model happens to be open.
