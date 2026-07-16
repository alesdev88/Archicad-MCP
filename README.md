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

## Development

```bash
uv sync && uv run pytest          # offline suite
uv run pytest -m live -v          # against a running Archicad (test models only!)
```
