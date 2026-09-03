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
- **Nothing else, if you install the extension.** The `.mcpb` carries its own
  Python interpreter and every dependency, so there is nothing to install first
  and nothing to download on first launch. The manual install paths below do
  need **[uv](https://docs.astral.sh/uv/)**, which fetches a suitable Python
  (3.12+) for you.
- **[Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation/releases)**,
  optional but recommended. Required for element creation, issues, IFC checks,
  highlighting, and publishing. The bundled command definitions are synced from
  Tapir **1.5.8**, and the server has been exercised live against **1.5.9**, so
  a newer add-on than the definitions is fine. Without the add-on, those tools
  degrade instead of erroring.

## Install as a Claude Desktop extension (recommended)

One file, one click, no JSON editing, and no prerequisites. Download the bundle
for your platform from the
[latest release](https://github.com/alesdev88/Archicad-MCP/releases/latest):

| Platform | File |
|---|---|
| Windows | `archicad-mcp-0.5.1-win32.mcpb` |
| macOS (Apple silicon) | `archicad-mcp-0.5.1-darwin-arm64.mcpb` |

There is no Intel macOS bundle. `cryptography`, which this server depends on
through FastMCP, no longer publishes macOS x86_64 wheels, so that bundle could
only be produced by compiling on an Intel Mac. Intel Macs use the manual install
below instead, where the build happens on the machine that will run it.

Then in Claude Desktop open **Settings > Extensions** and drag it in.

Mode, office rules folder, and the property-read ceiling then appear as form
fields in the extension's settings, and the whole server gets an on/off switch.
Leave a field empty and it falls back to the default in the table below.

The bundle contains a complete CPython 3.12 and every dependency, so it starts
immediately and works on a machine with no Python, no uv, and no internet
access. That is why it is 42 MB on Windows and 54 MB on macOS: the alternative
was asking every machine to install a package manager first.

Deploying to a team? On a Team or Enterprise plan an owner can upload the bundle
under **Organization settings > Connectors > Desktop**, which makes it a
one-click install for everyone instead of a file to pass around.

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
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.5.1/archicad_mcp-0.5.1-py3-none-any.whl

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
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.5.1/archicad_mcp-0.5.1-py3-none-any.whl

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
uv tool install https://github.com/alesdev88/Archicad-MCP/releases/download/v0.5.1/archicad_mcp-0.5.1-py3-none-any.whl
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
archicad-mcp: Archicad 29 (build 5101) on port 19723, project 'Sample', Tapir 1.5.9
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

The server loads rules from **one directory**, and where that directory is set
depends on how the server was installed. Nothing else is read: the bundled
examples load only when no directory is set.

| Install | Where to set the rules directory |
|---|---|
| Claude Desktop extension (`.mcpb`) | Claude Desktop > Settings > Extensions > Archicad > **Office rules folder**. This fills `ARCHICAD_MCP_RULES_DIR` for you; the field is empty after install and stays empty until you set it. |
| `uv tool install` + Claude Desktop config | `"env": { "ARCHICAD_MCP_RULES_DIR": "/absolute/path/to/office-rules" }` on the server entry in `claude_desktop_config.json`, as in the examples above. |
| `uv tool install` + Claude Code | `claude mcp add archicad -e ARCHICAD_MCP_RULES_DIR=/absolute/path/to/office-rules -- archicad-mcp --mode full`, or edit the entry's `env` in `~/.claude.json`. |
| Any shell | `archicad-mcp --rules-dir /absolute/path/to/office-rules`, or export `ARCHICAD_MCP_RULES_DIR`. The flag wins over the variable. |

Use an absolute path. A relative one resolves against whatever working
directory the client happened to spawn the server in.

Check what actually loaded before trusting an audit. The startup line in the
log says where the rules came from and how many there are:

```
archicad-mcp: mode=full, 1 rule loaded from /Users/YOU/office-rules
archicad-mcp: mode=full, 3 bundled example rules loaded (no rules directory set)
```

and `list_rules` returns the same `source` plus every rule id and any file that
failed to parse. A low count with the right directory usually means the file
holds templates that are still commented out, which is how the starter file
ships. The count is of rules, not files.

A rule file is a YAML **list** of rules:

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

Mesh models (OBJ, 3DS) become placeable Archicad library parts without opening
the GDL editor. The pipeline parses the mesh (units, pivots, welding),
optionally decimates dense meshes through a background Blender, writes the HSF
source, compiles it with the LP_XMLConverter bundled inside Archicad, and
deploys over the same connection the server uses. Finish variants become
dropdowns in Object Settings. See **[the GDL pipeline guide](docs/gdl-pipeline.md)**.

There are two ways in.

**From an MCP client**, using `list_gdl_sources`, `inspect_gdl_source`,
`build_gdl_object` and `deploy_gdl_object`. Set the **GDL workspace folder** in
the extension settings, and add that same folder to Archicad once via File >
Libraries and Objects > Library Manager. Source meshes and textures go in it by
hand; everything the tools write lands there too. Building needs no project
open. Deploying reloads libraries, places the object, renders it and returns the
image, then deletes the instance it placed unless you pass `keep=true`.

That render is the point. Archicad silently drops defective 3D bodies while
every offline validator passes them, so looking at the picture is the only
automated check that catches it.

This route runs inside the server process, so it works from clients whose agent
has no shell on the machine running Archicad, which includes any sandboxed one.

**From a shell**, using the `archicad-gdl` command:

```bash
archicad-gdl build chair.3ds --name "My Chair" --config assets.json
archicad-gdl deploy "build/My Chair.gsm" --place 0 0 --preview check.png
```

The command line tool comes with the `uv tool install` paths above and not with
the Claude Desktop extension, which bundles an interpreter for its own use
rather than putting anything on your PATH.

## Tools

**QA (both modes):** `list_instances`, `get_model_summary`, `list_rules`,
`run_rule`, `audit_delivery_readiness`, `verify_ifc_export_readiness`,
`highlight_failures`, `create_issues_from_failures`

**Core (full mode):** `find_elements`, `search_definitions`, `get_element_data`,
`set_element_data`, `create_elements`, `move_elements`, `delete_elements`,
`get_selection`, `set_selection`, `clear_selection`, `get_project_info`,
`list_attributes`, `list_issues`, `create_issue`, `add_issue_comment`,
`attach_elements_to_issue`, `export_issues_bcf`, `import_issues_bcf`, `publish`,
`read_schedule_scheme`, `edit_schedule_scheme`, `validate_schedule_scheme`.
Every write is dry-run by default; delete and move also require `confirm=true`.
No other Archicad MCP server does this: Graphisoft's own writes on the first
call, so an agent pointed at a live project has no rehearsal step there.

`find_elements` is a criteria query in the shape of Archicad's Find & Select:
groups of property comparisons, AND or OR within a group, OR between groups,
an element-type filter per group, 22 operators including string matching,
classification branch tests and the four senses of "empty". `search_definitions`
is the discovery step before it: fuzzy, accent-insensitive search over property
and attribute definitions that returns the exact property address the other
tools accept and whether the value can be written. Both are documented in
[the query guide](docs/query.md).

**Teamwork (full mode):** `reserve_elements`, `release_elements`. Both are
confirm-gated. A dry run reports what can be known without touching the
server: unknown GUIDs and elements already in your workspace. Who holds the
rest is only learned by attempting, because Archicad exposes no read for it;
with `confirm=true` the result separates reserved, reserved by others (with the
user's name), already mine, not found, and indirectly reserved. Verified live:
reserving one door also pulled in its wall and the wall's other door, and the
tool reported both. Sending and receiving stay in the gateway. Needs Tapir.

**Gateway (full mode):** `list_api_commands`, `describe_api_command`,
`execute_read_api_command`, `execute_write_api_command`. The complete official +
Tapir command surface (309 commands on the verified setup), for anything the
curated tools don't cover.

**Library parts (full mode):** `list_gdl_sources`, `inspect_gdl_source`,
`build_gdl_object`, `deploy_gdl_object`. Turn mesh models (OBJ, 3DS) into
placeable Archicad library parts with finish variants, without opening the GDL
editor. Requires the GDL workspace folder to be set and added as a linked
library in Archicad once. See [the GDL pipeline guide](docs/gdl-pipeline.md).

Reads and writes are separate tools throughout, and every tool declares whether
it is read-only or destructive. Clients use those declarations to decide what to
run without asking you: a read never prompts, a write always does. The gateway
splits the command catalog the same way, 138 reads and 171 writes, classified by
command name with anything unrecognised treated as a write. The write half also
refuses to run without `confirm=true`, because it can reach `DeleteElements` and
`QuitArchicad`.

## Development

```bash
uv sync && uv run pytest          # offline suite
```

To install unreleased `main` rather than a release, point uv at the repository
instead of at a wheel, or append a tag to build a released version from source:

```bash
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git          # main
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git@v0.5.1   # a release
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

Build the Claude Desktop extensions. One bundle per platform, both from this
one machine (Node is needed, for the `mcpb` packer):

```bash
uv run python scripts/build_bundle.py --target all
```

Each bundle gets a relocatable CPython from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
with every locked dependency installed into its own site-packages, so it starts
with no uv, no system Python and no network. The tree contains compiled wheels,
which is why a bundle is platform-specific; the build is not, because
`uv pip install --python-platform` resolves wheels for a named target. Source
builds are refused outright: one would compile for *this* machine and put the
result in a bundle labelled for another, which fails at import on the user's
machine with nothing to explain why.

That refusal is also why there is no Intel macOS bundle. The script self-tests
the bundle it just built whenever the target is the machine building it, and
says so when it cannot, which is every time you cross-build for Windows.

The version is written in four places (`pyproject.toml`, `manifest.json`,
`server.json`, and the download links in this README) and the test suite fails
if they drift:

```bash
uv run python scripts/check_release_version.py
```

Releasing is a tag push. `.github/workflows/release.yml` refuses the tag unless
all four files and the tag itself agree, then builds both bundles, the wheel and
the sdist, attaches them to a GitHub release, stamps each bundle's SHA-256 into
`server.json`, and publishes that to the MCP registry. Run the check by hand
first, because a pushed tag has to be deleted before it can be corrected, and
the registry refuses a version it already holds:

```bash
uv run python scripts/check_release_version.py v0.5.1
git tag v0.5.1 && git push origin v0.5.1
```

A cross-built Windows bundle cannot be executed by the machine that built it,
so install one on Windows before trusting a release. The 0.2.1 bundle was
checked that way and runs. Each new release should be tested on Windows before
being used in production.

`icon.png` is rasterised from `icon.svg`, so the mark stays editable as vector
art: change the SVG, then redraw the PNG the bundle ships. Pillow does that
rasterising. It used to be pulled in just for this step, but the GDL pipeline
now needs it to downscale textures, so it is a project dependency and the
script can use it directly:

```bash
uv run python scripts/make_icon.py
```

## Docs

- **[Known issues](docs/known-issues.md)**: the property-read crash, the element
  ceiling, verified property names, and what is validated end-to-end.
- **[Writing rules](docs/rules.md)**: every rule type, field, and the scoring model.
- **[Schedule criteria codes](docs/scheme-criteria-codes.md)**: the empirical
  `Param_Type` and `Relation_Index` table, and how to extend it.
- **[GDL pipeline](docs/gdl-pipeline.md)**: mesh models to library parts with
  finish dropdowns, and the GDL fine print the generator encodes.
- **[API dashboard](https://alesdev88.github.io/Archicad-MCP/api-dashboard.html)**:
  every one of the 309 reachable commands, grouped, showing which have a
  dedicated tool and which are gateway-only. Generated rather than written;
  refresh it with `uv run python scripts/build_dashboard.py` after a Tapir
  definitions sync, and the push publishes it.

## Privacy Policy

The server runs entirely on your machine and makes no outbound network
connections. It talks to the Archicad JSON API on `127.0.0.1`, ports 19723 to
19743, and to nothing else. There is no telemetry, no analytics, and no backend:
the author receives nothing, including error reports.

Model data the server reads is returned to the MCP client that asked for it,
normally Claude Desktop, which sends it to Anthropic as part of your
conversation under [Anthropic's Privacy
Policy](https://www.anthropic.com/legal/privacy). Nothing is cached or retained
by the server between requests. Two reductions are built in: `verdicts` mode
keeps the project name out of what the model sees, and Teamwork credentials are
stripped from `get_project_info` before it returns.

Full text: **[PRIVACY.md](PRIVACY.md)**.

## License

MIT. See [LICENSE](LICENSE).
