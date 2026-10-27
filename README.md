# Archicad MCP

Connects Claude, ChatGPT or any MCP client to a running **Archicad 29** (30 works
too) on macOS and Windows. You ask in plain language; the server does it through
Archicad's JSON API and the
[Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) add-on.

- **Check a model against office standards.** YAML rules run against the open
  model: pass or fail, a score, and the failing elements, highlighted or turned
  into issues.
- **Find, read and change elements.** Criteria queries, properties,
  classifications, GDL and IFC data, favourites, attributes, clash checks.
- **Create elements.** Walls, slabs, columns, doors and windows, zones, stairs,
  dimensions and 30-odd more types.
- **Everything else.** About 500 official and Tapir commands through one gateway.

> [!WARNING]
> **Save before reading properties.** Archicad 29 can crash on a property read,
> even of one element, taking unsaved work with it. The server cannot prevent it.
> Ask the client to run `save_project` first, and see
> [Known issues](docs/known-issues.md).

## Install

**Claude Desktop:** download the bundle for your platform from the
[latest release](https://github.com/alesdev88/Archicad-MCP/releases/latest) and
drag it into **Settings > Extensions**. It carries its own Python, so there is
nothing else to install.

| Platform | File |
|---|---|
| Windows | `archicad-mcp-0.11.0-win32.mcpb` |
| macOS (Apple silicon) | `archicad-mcp-0.11.0-darwin-arm64.mcpb` |

**Claude Code** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install archicad-mcp-server
claude mcp add archicad -- archicad-mcp --mode full
```

**Tapir:** most tools need the Tapir add-on in Archicad. Ask the client to
**install Tapir**, or run `archicad-mcp install-tapir`. It puts the pinned Tapir
1.7.3 into Archicad's Add-Ons folder, checked by sha256, and never replaces an
existing one unless told to. Restart Archicad afterwards.

Intel Macs, a hand-written Claude Desktop config, ChatGPT, Codex and all the
settings: [Install](docs/install.md).

## Check it works

With Archicad open, ask the client to **list Archicad instances**. It shows the
port, Archicad version, open project and Tapir version, and what to install when
something is missing. If the client shows no tools at all, see
[Check it works](docs/install.md#check-it-works).

## What you can ask

| Area | Tools |
|---|---|
| QA | `audit_delivery_readiness`, `run_rule`, `list_rules`, `verify_ifc_export_readiness`, `highlight_failures`, `create_issues_from_failures` |
| Find and read | `find_elements`, `search_definitions`, `get_element_data`, `get_model_summary`, `get_project_info`, `list_attributes`, `find_collisions` |
| Change | `set_element_data`, `modify_elements`, `apply_favorites`, `move_elements`, `delete_elements`, `edit_attributes`, `edit_property_definitions`, `edit_classifications` |
| Create | `create_elements` (40 types), `create_swept_beam`, `create_road_marking`, and `learn_library_part` for placing furniture, doors and windows from a sketch |
| Look | `show_elements`, `capture_window`, the selection tools |
| Issues and output | Issue Manager and BCF, `export_ifc`, `publish`, `save_project` |
| Teamwork | `reserve_elements`, `release_elements` |
| Schedules | `read_schedule_scheme`, `edit_schedule_scheme`, `validate_schedule_scheme`, through Scheme Settings' XML (Archicad has no schedule API) |
| Library parts | mesh models (OBJ, 3DS) to GDL objects: `build_gdl_object`, `deploy_gdl_object` |
| Gateway | `list_api_commands`, `describe_api_command`, `execute_read_api_command`, `execute_write_api_command` |

Every tool in detail, with what it checks and what it needs: [Tools](docs/tools.md).

**Safe by default.** Changes to elements, data and definitions are a dry run
first, showing current and new values. Delete and move also need `confirm=true`.
Elements Archicad would refuse (another view's, a hidden or locked layer, a
hotlinked module, not reserved in Teamwork) are skipped with the reason, never
unlocked. A commit reads back what it wrote and reports what did not land.

## Office rules

Rules are YAML files in a folder you point the server at (the extension's
**Office rules folder** setting, or `ARCHICAD_MCP_RULES_DIR`):

```yaml
- id: walls-fire-rating
  type: property-required
  property: "OFFICE/Fire Rating"
  applies_to: { element_type: Wall }
  severity: error
```

Six rule types are built in, and custom checks go in Python beside the YAML. Keep
real office standards outside this repository. See [Writing rules](docs/rules.md).

## Docs

- [Install](docs/install.md): every install route, Tapir, configuration
- [Tools](docs/tools.md): every tool, what it checks and what it needs
- [Known issues](docs/known-issues.md)
- [Finding elements](docs/query.md) and
  [reading and writing element data](docs/element-data.md)
- [Writing rules](docs/rules.md)
- [Schedules](docs/schedules.md)
- [GDL pipeline](docs/gdl-pipeline.md) and [scripting](docs/scripting.md)
- [API dashboard](https://alesdev88.github.io/Archicad-MCP/api-dashboard.html):
  every reachable command, and which have a tool
- [Development](docs/development.md): tests, Tapir, bundles, releasing

## Privacy

The server runs on your machine and talks only to Archicad on `127.0.0.1`. There
is no telemetry and no backend. What it reads goes to the MCP client that asked,
normally Claude, under that client's privacy terms. See [PRIVACY.md](PRIVACY.md).

## License

MIT. See [LICENSE](LICENSE).
