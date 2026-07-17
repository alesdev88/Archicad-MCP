# Archicad MCP

MCP server for **Archicad 29** (macOS + Windows). Connects Claude Desktop,
Claude Code, or any MCP client to a *running* Archicad instance.

Two things in one server:

1. **Delivery-readiness QA** — a local rules engine (your standards as YAML)
   returning verdicts: pass/fail, scores, failing element GUIDs.
2. **Full API access** — curated tools for querying, editing, and creating
   elements, plus a gateway to every official JSON API and
   [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) command.

## Privacy: pick your mode

| Mode | Tools exposed | Model data sent to the AI |
|---|---|---|
| `--mode verdicts` | 8 QA tools | Verdicts only: rule ids, counts, GUIDs. Never element names, property values, or project info. |
| `--mode full` (default) | everything | Raw model data flows to the AI by design. |

Claims like "no data leaves your computer" don't apply to any MCP server —
tool *results* go to the model. In `full` mode, treat the model contents as
shared with your AI provider; use `verdicts` mode for confidential projects.

## Requirements

- Archicad 29 running with a project open (the JSON API talks to the live app)
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Optional but recommended: the [Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation/releases)
  — required for element creation, issues, IFC checks, highlighting, publishing

## Install

```bash
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git
```

## Configure Claude Desktop

macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
Windows — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "archicad": {
      "command": "archicad-mcp",
      "args": ["--mode", "full"],
      "env": { "ARCHICAD_MCP_RULES_DIR": "/path/to/your/rules" }
    }
  }
}
```

Claude Code:

```bash
claude mcp add archicad -- archicad-mcp --mode full
```

## Writing rules

Point `ARCHICAD_MCP_RULES_DIR` (or `--rules-dir`) at a directory of YAML files:

```yaml
- id: walls-fire-rating
  type: property-required
  property: "OFFICE/Fire Rating"   # user properties: "Group/Name"
  applies_to: { element_type: Wall }
  severity: error
  tags: [ifc-delivery]
```

Rule types: `property-required`, `classification-required`, `layer-compliance`,
`zone-number-required`, `ifc-property-required`. Custom logic goes in
`custom_rules.py` in the same directory (module-level `RULES = [...]`).
Without a rules dir, bundled example rules load. Keep office standards out of
public repos.

## Tools

**QA (both modes):** `list_instances`, `get_model_summary`, `list_rules`,
`run_rule`, `audit_delivery_readiness`, `verify_ifc_export_readiness`,
`highlight_failures`, `create_issues_from_failures`

**Core (full mode):** `query_elements`, `get_element_data`, `set_element_data`,
`create_elements`, `move_elements`, `delete_elements`, `manage_selection`,
`get_project_info`, `list_attributes`, `manage_issues`, `publish`
— every write is dry-run by default; delete/move require `confirm=true`.

**Gateway (full mode):** `list_api_commands`, `describe_api_command`,
`execute_api_command` — the complete official + Tapir command surface.
Refresh Tapir schemas after add-on updates: `uv run python scripts/sync_tapir_defs.py`.

## Known issues

**Reading property values can crash Archicad — this is the big one.**
`GetPropertyValuesOfElements` has crashed Archicad 29.0 build 4006 three times
during development (`ComposeResult` abort, taking unsaved work with it). It is
**not** a volume problem: the third crash was a **single property on a single
element** (a user-defined property read on a freshly created slab). Reading
built-in properties (e.g. `ModelView_LayerName`) across thousands of real
elements worked fine, so the trigger appears to be *a specific
property/element combination* — most likely a property that is not applicable to
that element, which the API aborts on instead of returning a per-element error.

This is an Archicad-side fault the server can trigger but **cannot prevent**.
The element ceiling below limits blast radius; it does **not** make property
reads safe. Treat any `get_element_data` / `set_element_data` / audit against a
model you care about as capable of crashing it, and save first.

The server refuses a property fetch spanning more than
`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` elements (default 5000). Consequences:

- `get_model_summary` returns `by_type` only by default (safe); pass
  `include_layer_story=true` for the per-element breakdown (refused on very
  large models).
- `audit_delivery_readiness` now scopes its property fetch to the element types
  the rules target (a rule's `applies_to`), so a typed audit (e.g. fire-rating
  on walls) reads only those elements. An audit is refused only when a rule
  targets *all* elements and the model exceeds the ceiling — scope that rule, or
  raise the env var.

**Built-in property names — verified.** Confirmed against a live Archicad 29.0
model (2026-07-16): the layer name is `ModelView_LayerName` (there is no
`General_LayerName`), zone number/name are `Zone_ZoneNumber` / `Zone_ZoneName`,
and an element's **story is not a property** — it comes from Tapir
`GetDetailsOfElements.floorIndex` (a 0-based index, so `query_elements(story=…)`
and `get_model_summary` breakdowns key on floorIndex).

**Live-validated (Archicad 29.0/4006, 16k-element model):** instance discovery,
`get_model_summary`, `query_elements` (type + story filters), `get_element_data`
(built-in properties + classifications), `manage_selection` (get/set/clear —
`set` replaces), `create_elements` → `move_elements` → `delete_elements` with
their dry-run and confirm guards, and the tier-3 gateway (231 commands).

**Still not validated live:** `set_element_data` commit (its dry-run read
crashed Archicad — see above), `manage_issues`, `publish`, and a full audit. Run
the read-only live canary against a small non-sensitive model, port pinned:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

## Development

```bash
uv sync && uv run pytest          # offline suite
# Live: open a SMALL non-sensitive test model, then pin the port explicitly.
# Never run live tests against a client or teamwork project.
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```
