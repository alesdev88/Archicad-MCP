# Install

The short version is in the [README](../README.md#install). This page has the
other routes, the Tapir installer in full, and the settings.

## Requirements

- **Archicad 29**, running, with a project open. Archicad 30 works too. The
  server talks to the live app over its JSON API.
- **Nothing else for the Claude Desktop extension.** The `.mcpb` carries its own
  Python and every dependency. The manual installs below need
  **[uv](https://docs.astral.sh/uv/)**, which fetches a suitable Python (3.12+).
- **[Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation)**, optional
  but recommended, and installed for you (see [Tapir](#tapir)). Element creation,
  editing, issues, IFC checks, highlighting and publishing need it. Without it,
  the tools that need it say which file to install and the rest degrade instead
  of failing.

## Claude Desktop extension

Download the bundle for your platform from the
[latest release](https://github.com/alesdev88/Archicad-MCP/releases/latest),
open **Settings > Extensions** in Claude Desktop and drag it in. Mode, office
rules folder and the property-read ceiling then appear as fields in the
extension's settings. An empty field falls back to the default under
[Configuration](#configuration).

The bundle holds a complete CPython 3.12 and every dependency, so it starts on a
machine with no Python, no uv and no internet access. That is why it is 42 MB on
Windows and 54 MB on macOS.

There is no Intel macOS bundle: `cryptography`, which the server depends on
through FastMCP, no longer publishes macOS x86_64 wheels, so that bundle could
only be built on an Intel Mac. Intel Macs use the manual install below.

On a Team or Enterprise plan an owner can upload the bundle under
**Organization settings > Connectors > Desktop**, which makes it a one-click
install for everyone.

## Manual install

The manual routes install the server from PyPI, where it is published as
`archicad-mcp-server` (the plain name was taken), so
`uv tool upgrade archicad-mcp-server` moves you to the next release. To pin a
release, install `archicad-mcp-server==<version>` instead.

### macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv, if you do not have it
uv tool install archicad-mcp-server
which archicad-mcp                                    # ~/.local/bin/archicad-mcp
```

Then edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Use the absolute path: Claude Desktop does not inherit your shell's `PATH`, so a
bare `"archicad-mcp"` usually fails to start. Restart Claude Desktop afterwards.

### Windows

```powershell
winget install --id=astral-sh.uv -e                  # uv, if you do not have it
uv tool install archicad-mcp-server
where.exe archicad-mcp                                # %USERPROFILE%\.local\bin\archicad-mcp.exe
```

Then edit `%APPDATA%\Claude\claude_desktop_config.json`:

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

Backslashes are doubled in JSON, and the `.exe` matters. Restart Claude Desktop
afterwards.

### Claude Code

Claude Code inherits your shell's `PATH`, so the bare command works:

```bash
uv tool install archicad-mcp-server
claude mcp add archicad -- archicad-mcp --mode full
```

### Codex

OpenAI's Codex CLI runs local servers directly, so nothing leaves the machine:

```bash
codex mcp add archicad -- archicad-mcp --mode full
```

### ChatGPT

ChatGPT only talks to remote MCP servers over HTTPS, and this server has to run
on the machine that runs Archicad. So: serve over HTTP on that machine, tunnel it
to a public HTTPS URL, and register the URL as a custom connector.

> [!CAUTION]
> Whoever reaches that URL drives your open Archicad model: every property is
> readable, and in `full` mode elements can be edited, deleted and published.
> ChatGPT custom connectors authenticate with OAuth or not at all, so this
> server adds no token of its own. Put access control in front of the tunnel
> (Cloudflare Access, ngrok's OAuth or IP restrictions), prefer `--mode
> verdicts`, and stop the server when you are done.

```bash
archicad-mcp --mode verdicts --transport http --http-port 8000   # binds to localhost only
cloudflared tunnel --url http://localhost:8000                   # or: ngrok http 8000
```

The MCP endpoint is the tunnel's public URL plus `/mcp`. In ChatGPT on the web,
turn on **Settings > Connectors > Advanced > Developer mode** (Plus, Pro,
Business, Enterprise or Edu), then **Settings > Connectors > Create**: name it
`Archicad`, paste `https://<your-tunnel>/mcp`, tick *I trust this provider*. In a
new chat, enable it under **+ > More > Developer mode**; this is per chat.

## Tapir

Tapir is a C++ add-on that runs inside Archicad, so it has to sit in Archicad's
Add-Ons folder. The server installs it:

```bash
archicad-mcp install-tapir --dry-run
```

That lists every Archicad on the computer and the Tapir each one has, downloads
the pinned Tapir release for each, checks every file's sha256 against the value
committed in this repository, and says what it would do. Without `--dry-run` it
shows the same plan and asks before writing anything. Restart Archicad
afterwards. From the Claude Desktop extension, which puts no command on your
PATH, ask Claude to run the `install_tapir` tool instead: a dry run first, then
`confirm=true` to write.

| Flag | What it does |
|---|---|
| `--archicad 30` | Only this Archicad version (repeatable). |
| `--dry-run` | Download and check, write nothing. |
| `--replace` | Replace an existing Tapir (see below). |
| `--yes` | Answer the prompts, for scripts. Never implies `--replace`. |
| `--from-file PATH` | A release file you downloaded yourself (with one `--archicad`); still checked. |
| `--latest` | The newest Tapir release instead of the pinned one, checked against GitHub's sha256. |
| `--add-ons-folder PATH` | An Add-Ons folder it does not find on its own (with one `--archicad`). |

**An existing Tapir is never touched by default.** It is listed with its path,
its date and whether it is identical to the pinned release, and skipped. With
`--replace` (or `replace_existing=true`) it is moved, never deleted, to
`~/Library/Application Support/archicad-mcp/tapir-backups/AC<version>-<date>/`
(`%LOCALAPPDATA%\archicad-mcp\tapir-backups\` on Windows), with a `backup.json`
saying where each piece came from. A custom Tapir build loses its own commands
when it is replaced. A Tapir that Archicad loads from another folder through
Add-On Manager is left alone; remove it there first.

Tapir goes into `Add-Ons/Tapir/`, where Tapir's own installer puts it, so
Tapir's **Check for Updates** keeps working.

**When the Add-Ons folder needs administrator rights:** on macOS the system
password dialog appears. On Windows, where Archicad lives under Program Files,
the result gives the exact command to run from a terminal opened as
administrator, plus the steps to hand to IT. The Windows path is covered by unit
tests only so far.

**By hand:** download `TapirAddOn_AC<version>_Mac.zip` or
`TapirAddOn_AC<version>_Win.apx` for your Archicad version from the
[Tapir 1.7.3 release](https://github.com/ENZYME-APD/tapir-archicad-automation/releases/tag/1.7.3),
unzip it (macOS) or copy it (Windows) into `Add-Ons/Tapir/` inside the Archicad
folder, and restart Archicad.

The bundled command definitions come from Tapir 1.7.3. A newer add-on than the
definitions is fine; a tool that needs a newer Tapir than the one installed
refuses before sending anything and names both versions.

## Check it works

With Archicad open, ask the client to **list Archicad instances**. `list_instances`
reports the port, version, open project and whether Tapir answered, and a
`tapir_hint` naming the file to install when Tapir is missing or too old. If no
Archicad is found, see [Known issues: connection](known-issues.md#connection).

If the client shows no tools at all, the server never started. Read the log; the
server writes what it found to stderr on startup, which Claude Desktop keeps:

```bash
tail -20 ~/Library/Logs/Claude/mcp-server-archicad.log   # %APPDATA%\Claude\logs on Windows
```

```
archicad-mcp: mode=full, 12 rules loaded
archicad-mcp: Archicad 29 (build 5101) on port 19723, project 'Sample', Tapir 1.7.3
```

No line at all means the server did not start. Otherwise the line says whether
Archicad is running (tools connect on demand once it is) and, when Tapir is
missing, which tools degrade and the install command.

## Configuration

| Flag | Env var | Default | What it does |
|---|---|---|---|
| `--mode` | `ARCHICAD_MCP_MODE` | `full` | `full` or `verdicts` (below) |
| `--rules-dir` | `ARCHICAD_MCP_RULES_DIR` | bundled examples | Folder of YAML rule files |
| `--port` | n/a | auto-detect `19723`-`19743` | Pin when several Archicads run at once |
| `--transport` | `ARCHICAD_MCP_TRANSPORT` | `stdio` | `stdio` for Claude Desktop, Claude Code and Codex; `http` (Streamable HTTP at `/mcp`) for ChatGPT and other remote clients |
| `--host` | n/a | `127.0.0.1` | Bind address, `--transport http` only |
| `--http-port` | n/a | `8000` | Listen port, `--transport http` only. Not the Archicad port |
| `--enable-scripts` | `ARCHICAD_MCP_SCRIPTS` | off | Expose `run_script` and `apply_changeset` ([Scripting](scripting.md)) |
| n/a | `ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` | `5000` | Refuse property reads spanning more elements than this |

| `--mode` | Tools exposed |
|---|---|
| `full` (default) | Everything: QA, the element tools and the API gateway. |
| `verdicts` | The 9 QA tools only: rule ids, counts and failing GUIDs, with no project name from `list_instances`. Element counts still reach the model, layer names too with `include_layer_story=true`. |

### The rules folder

The server loads rules from one folder. The bundled examples load only when none
is set.

| Install | Where to set it |
|---|---|
| Claude Desktop extension | Settings > Extensions > Archicad > **Office rules folder** |
| Manual install + Claude Desktop | `"env": { "ARCHICAD_MCP_RULES_DIR": "/absolute/path" }` in `claude_desktop_config.json`, as above |
| Claude Code | `claude mcp add archicad -e ARCHICAD_MCP_RULES_DIR=/absolute/path -- archicad-mcp --mode full` |
| Any shell | `archicad-mcp --rules-dir /absolute/path`, or export `ARCHICAD_MCP_RULES_DIR`. The flag wins. |

Use an absolute path; a relative one resolves against wherever the client
started the server. The startup line in the log says where the rules came from
and how many loaded, and `list_rules` returns the same plus any file that failed
to parse:

```
archicad-mcp: mode=full, 1 rule loaded from /Users/YOU/office-rules
archicad-mcp: mode=full, 3 bundled example rules loaded (no rules directory set)
```

A low count from the right folder usually means the file still holds the
commented-out templates it ships with. See [Writing rules](rules.md).
