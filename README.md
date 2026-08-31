# Archicad MCP

An MCP server for **Archicad 29** on macOS and Windows. It connects Claude
Desktop, Claude Code, or any MCP client to a *running* Archicad instance and
does two jobs:

1. **Delivery-readiness QA.** Your office standards, written as YAML rules and
   run against the open model. Returns pass/fail, a score, and the GUIDs of the
   elements that failed.
2. **Full API access.** Curated tools for querying, editing, and creating
   elements, plus a gateway to every official JSON API and
   [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) command.

> [!WARNING]
> **Save before you read properties.** `GetPropertyValuesOfElements` can crash
> Archicad 29, even for a single property on a single element, taking unsaved
> work with it. This is an Archicad-side fault the server can trigger but cannot
> prevent. It affects `audit_delivery_readiness`, `run_rule`, `get_element_data`,
> and `set_element_data`. See [Known issues](docs/known-issues.md) before you
> point this at a model you care about.

## Requirements

- **Archicad 29**, running, with a project open. The JSON API talks to the live app.
- **[uv](https://docs.astral.sh/uv/)**, which installs the server and fetches a
  suitable Python (3.12+) for you.
- **[Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation/releases)**,
  optional but recommended. Required for element creation, issues, IFC checks,
  highlighting, and publishing; verified on Tapir 1.5.8. Without it, those tools
  degrade instead of erroring.

## Install as a Claude Desktop extension (recommended)

One file, one click, no JSON editing. Download `archicad-mcp-0.1.2.mcpb` from the
[latest release](https://github.com/alesdev88/Archicad-MCP/releases/latest), then
in Claude Desktop open **Settings > Extensions** and drag it in.

Mode, office rules folder, and the property-read ceiling then appear as form
fields in the extension's settings, and the whole server gets an on/off switch.
Leave a field empty and it falls back to the default in the table below.

You still need [uv](https://docs.astral.sh/uv/) on the machine: the extension
uses it to build its own environment on first launch, which takes a few seconds
the first time and is instant afterwards.

If you would rather wire it up by hand, or you are on Claude Code, use one of
the sections below instead. Those install the wheel from a tagged release, so
you get a known version rather than whatever `main` happens to be. To upgrade,
re-run the install command with the newer version's URL from the
[releases page](https://github.com/alesdev88/Archicad-MCP/releases).

## Install on macOS

```bash
# 1. Install uv (skip if you already have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the server from the latest release
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.1.2/archicad_mcp-0.1.2-py3-none-any.whl

# 3. Note the path (you need it for the config below)
which archicad-mcp        # ~/.local/bin/archicad-mcp
```

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "archicad": {
      "command": "/Users/YOU/.local/bin/archicad-mcp",
      "args": ["--mode", "full"],
      "env": { "ARCHICAD_MCP_RULES_DIR": "/Users/YOU/office-rules" }
    }
  }
}
```

Use the **absolute path**. Claude Desktop does not inherit your shell's `PATH`,
so a bare `"archicad-mcp"` usually fails to spawn. Restart Claude Desktop after
editing the file.

## Install on Windows

```powershell
# 1. Install uv (skip if you already have it)
winget install --id=astral-sh.uv -e

# 2. Install the server from the latest release
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.1.2/archicad_mcp-0.1.2-py3-none-any.whl

# 3. Note the path (you need it for the config below)
where.exe archicad-mcp    # %USERPROFILE%\.local\bin\archicad-mcp.exe
```

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "archicad": {
      "command": "C:\\Users\\YOU\\.local\\bin\\archicad-mcp.exe",
      "args": ["--mode", "full"],
      "env": { "ARCHICAD_MCP_RULES_DIR": "C:\\Users\\YOU\\office-rules" }
    }
  }
}
```

Backslashes must be doubled in JSON, and the `.exe` matters. Restart Claude
Desktop after editing the file.

## Install for Claude Code

Claude Code inherits your shell's `PATH`, so the bare command name works:

```bash
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.1.2/archicad_mcp-0.1.2-py3-none-any.whl
claude mcp add archicad -- archicad-mcp --mode full
```

## Check it works

With Archicad open, ask the client to **list Archicad instances**. The
`list_instances` tool reports the port, version, open project, and whether Tapir
answered, which is the fastest way to tell a config problem from a connection
problem. If nothing is found, see
[Known issues: connection](docs/known-issues.md#connection).

If the client shows no tools at all, the server never started, and asking it
anything will not tell you why. Read the log instead. The server writes what it
found to stderr on startup, which Claude Desktop captures:

```bash
tail -20 ~/Library/Logs/Claude/mcp-server-archicad.log   # %APPDATA%\Claude\logs on Windows
```

```
archicad-mcp: mode=full, 12 rules loaded
archicad-mcp: Archicad 29 (build 4006) on port 19723, project 'Sample', Tapir 1.5.8
```

That line distinguishes the three failures that look identical from the chat
window: the server not spawning (no line at all), Archicad not running (the
line says so, and says tools connect on demand once you start it), and the
Tapir add-on missing (the line names which tools degrade).

## Configuration

| Flag | Env var | Default | What it does |
|---|---|---|---|
| `--mode` | `ARCHICAD_MCP_MODE` | `full` | `full` or `verdicts` (see below) |
| `--rules-dir` | `ARCHICAD_MCP_RULES_DIR` | bundled examples | Directory of YAML rule files |
| `--port` | n/a | auto-detect `19723`-`19743` | Pin when several Archicads run at once |
| n/a | `ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` | `5000` | Refuse property fetches spanning more elements than this |

### Modes

| `--mode` | Tools exposed |
|---|---|
| `full` (default) | Everything: QA, core, and the API gateway. |
| `verdicts` | The 8 QA tools only: rule ids, counts, and failing GUIDs, with no project name from `list_instances`. Element counts still reach the model, layer names included if you pass `include_layer_story=true`. |

## Rules

Point `ARCHICAD_MCP_RULES_DIR` (or `--rules-dir`) at a directory of YAML files:

```yaml
- id: walls-fire-rating
  type: property-required
  property: "OFFICE/Fire Rating"   # user properties are "Group/Name"
  applies_to: { element_type: Wall }
  severity: error
  tags: [ifc-delivery]
```

Five rule types ship built in (`property-required`, `classification-required`,
`layer-compliance`, `zone-number-required`, `ifc-property-required`), and custom
checks go in a `custom_rules.py` beside the YAML. Without a rules directory, the
bundled examples load so you have something to run.

Keep real office standards **outside this repo**, in a local rules directory.

Full reference: **[docs/rules.md](docs/rules.md)**.

## Schedules

Archicad exposes **no API for schedules at all**. Not the JSON API, not Tapir,
and per Graphisoft not the C++ API either. What it does support is the XML
round trip built into Scheme Settings, and that is what these tools work
through:

1. In Archicad: Document > Schedules > Scheme Settings, select a scheme, **Export**
2. Edit it: `read_schedule_scheme` to see what it does, `edit_schedule_scheme`
   to apply a YAML spec, `validate_schedule_scheme` to check its bindings
   against the open project
3. In Archicad: Scheme Settings > **Import**

A scheme spec looks like this:

```yaml
- id: door-schedule
  template: exports/door-scheme.xml
  name: "Door Schedule"
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating" }
      width: 40
```

A column binds three ways:

- `bind: { property: "<GUID>" }`, which needs no connection to Archicad, or a
  `"Group/Name"` string, which `edit_schedule_scheme` resolves by connecting
  to Archicad and looking the name up. A spec that only uses GUIDs (plus
  `gdl_param` and `builtin` bindings, below) runs fully offline; a spec with
  even one named property needs Archicad open with the project that defines
  it.
- `bind: { gdl_param: "<parameter name>" }`, a library part parameter by name
- `bind: { builtin: Quantity }` for the few named built-ins, or
  `bind: { builtin: { param_type: 0, param_index: -1561 } }` for any other
  built-in by its raw numbers

The named table deliberately holds only `Quantity`: the codes behind it are
undocumented and are being mapped empirically, one confirmed example at a
time. The raw-numbers form is what lets a scheme still be fully expressed
even when a built-in has no name yet, and this is not a rare corner case: on
a real 27-column door schedule, 2 columns need it.

A column can also carry `width: <number>`, which sets its cell width to
match. This is a no-op, reported as such, when the column already has that
width. Only the portrait width is guaranteed: the landscape width field is
updated too when a column already has one, but is never created on a column
that lacks it, since that has not been confirmed as a field Archicad itself
writes for every scheme, and the change log says so plainly rather than
guessing.

Criteria are read and preserved but not yet editable: the numeric codes behind
them are undocumented and are being mapped in
[docs/scheme-criteria-codes.md](docs/scheme-criteria-codes.md).

### Limitations

- **Criteria are read and preserved but cannot yet be edited.** See
  [docs/scheme-criteria-codes.md](docs/scheme-criteria-codes.md) for what is
  confirmed about the codes behind them so far, and what is still unknown.
- **Every edit needs two manual steps in Archicad**, Export before and
  Import after, because no API reaches schedules.
- **Whether re-importing an edited scheme updates it in place or creates a
  numbered duplicate is not yet confirmed.** Graphisoft's documentation says
  duplicate names are auto-numbered, but real exports carry stable scheme
  IDs, which suggests an in-place match may be possible. Test on a scratch
  project before relying on either behaviour.
- **`edit_schedule_scheme` refuses any file that would not survive a no-op
  save unchanged.** This protects the parts of the format the server does
  not model.

## Library parts

The `archicad-gdl` command (installed alongside the server) turns mesh models
(OBJ, 3DS) into placeable library parts and pushes them into the open
project, without opening the GDL editor:

```bash
archicad-gdl build chair.3ds --name "My Chair" --config assets.json
archicad-gdl deploy "build/My Chair.gsm" --place 0 0 --preview check.png
```

It parses the mesh (units, pivots, welding), optionally decimates dense
meshes through a background Blender, writes the HSF source, compiles it with
the LP_XMLConverter bundled inside Archicad, and deploys over the same
connection the server uses. Finish variants become dropdowns in Object
Settings. See **[the GDL pipeline guide](docs/gdl-pipeline.md)**.

## Tools

**QA (both modes):** `list_instances`, `get_model_summary`, `list_rules`,
`run_rule`, `audit_delivery_readiness`, `verify_ifc_export_readiness`,
`highlight_failures`, `create_issues_from_failures`

**Core (full mode):** `query_elements`, `get_element_data`, `set_element_data`,
`create_elements`, `move_elements`, `delete_elements`, `manage_selection`,
`get_project_info`, `list_attributes`, `manage_issues`, `publish`,
`read_schedule_scheme`, `edit_schedule_scheme`, `validate_schedule_scheme`.
Every write is dry-run by default; delete and move also require `confirm=true`.

**Gateway (full mode):** `list_api_commands`, `describe_api_command`,
`execute_api_command`. The complete official + Tapir command surface (231
commands on the verified setup), for anything the curated tools don't cover.

## Development

```bash
uv sync && uv run pytest          # offline suite
```

To install unreleased `main` rather than a release, point uv at the repository
instead of at a wheel, or append a tag to build a released version from source:

```bash
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git          # main
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git@v0.1.2   # a release
```

Live tests need a running Archicad. Open a **small, non-sensitive** test model
and pin the port explicitly. Never run these against a client or teamwork
project, and re-read the crash warning above first:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

After a Tapir add-on update, refresh the bundled command schemas:

```bash
uv run python scripts/sync_tapir_defs.py
```

Build the Claude Desktop extension. `version` in `manifest.json` and in
`pyproject.toml` have to state the same thing, and the test suite fails if they
drift:

```bash
uv run python scripts/check_release_version.py
npx @anthropic-ai/mcpb validate manifest.json && npx @anthropic-ai/mcpb pack . dist/archicad-mcp-0.1.2.mcpb
```

`.mcpbignore` decides what ships. The bundle carries `pyproject.toml` and
`uv.lock` rather than vendored wheels, so `uv` resolves the same pinned
dependency set on the target machine and one bundle serves both macOS and
Windows.

Releasing is a tag push. `.github/workflows/release.yml` refuses the tag unless
both files and the tag itself agree on the version, then builds the bundle, the
wheel, and the sdist and attaches all three to a GitHub release. Run the same
check by hand first, because a tag that has been pushed has to be deleted
before it can be corrected:

```bash
uv run python scripts/check_release_version.py v0.1.2
git tag v0.1.2 && git push origin v0.1.2
```

`icon.png` is generated, not hand-drawn, so it stays editable. Pillow is needed
only to redraw it and is deliberately not a project dependency:

```bash
uv run --with pillow python scripts/make_icon.py
```

## Docs

- **[Known issues](docs/known-issues.md)**: the property-read crash, the element
  ceiling, verified property names, and what is validated end-to-end.
- **[Writing rules](docs/rules.md)**: every rule type, field, and the scoring model.
- **[Schedule criteria codes](docs/scheme-criteria-codes.md)**: the empirical
  `Param_Type` and `Relation_Index` table, and how to extend it.
- **[GDL pipeline](docs/gdl-pipeline.md)**: mesh models to library parts with
  finish dropdowns, and the GDL fine print the generator encodes.

## License

MIT. See [LICENSE](LICENSE).
