# GDL Library Parts as MCP Tools: Design

**Date:** 2026-09-02
**Status:** Approved, not yet implemented. Gated on one live probe (see below).
**Repo:** https://github.com/alesdev88/Archicad-MCP.git

## Purpose

Make the GDL library-part pipeline reachable from an MCP client, so that
generating a `.gsm` from a mesh does not require a shell on the machine running
Archicad.

Today `archicad-gdl` is a console script over `src/archicad_mcp/gdl/`, declared
in `pyproject.toml` under `[project.scripts]`. None of the server's 33 MCP tools
build or deploy a library part. `create_elements(element_type="object")` maps to
Tapir `CreateObjects`, which places an instance of a library part that already
exists; it does not author one.

## What forced this

A colleague could not generate a GDL object while the same work succeeds
locally. The diagnosis, in order of significance:

1. The capability is a CLI, not a tool. An `.mcpb` install has no route to it on
   any platform. The bundle's `uv pip install` does place `archicad-gdl` in the
   bundle's own `runtime/bin`, but the extension only ever launches
   `python -m archicad_mcp.server`, so nothing invokes it.
2. His client (Cowork) runs the agent in a **Linux sandbox**. His MCP tools
   still reach Archicad because the server process runs on his own machine, as
   it must to talk to localhost. But the agent's *shell* is a remote container
   with no Archicad, hence no LP_XMLConverter, and no Blender.
3. `deploy` would fail even if a `.gsm` somehow existed in that sandbox.
   `embed_gsm` sends `str(gsm_path.resolve())` to Tapir as `inputPath`, and
   Archicad opens that path on **its own** filesystem. A sandbox path does not
   exist on his disk.
4. He is on Windows. Three macOS-only spots in the pipeline bite once a local
   shell is in play at all (see Windows portability).

The pipeline was designed on the assumption that the operator sits at the
machine running Archicad. Cowork and Claude Desktop both break that assumption,
and no amount of environment-variable configuration fixes it. Moving execution
into the server process is the fix, because that process already runs next to
Archicad by necessity.

## The assumption this design rests on, and the probe that must come first

The delivery mechanism chosen below is a **linked library folder**: the
workspace folder is added once to Archicad via Library Manager, builds overwrite
the `.gsm` on disk under a stable GUID, and `ReloadLibraries` updates placed
instances in place.

`docs/gdl-pipeline.md` recommends that setup, but the delivery path actually
demonstrated was the embedded library via `AddFilesToEmbeddedLibrary`. So the
following is **documented, not demonstrated**:

- that `ReloadLibraries` alone picks up a `.gsm` newly written into a linked
  library folder, with no Library Manager interaction, and
- that `CreateObjects` then resolves it by name.

Implementation opens with a single manual probe: drop a `.gsm` into a linked
folder by hand, reload, place, look. Nothing else gets built until it passes.

**Demonstrated 2026-09-03.** The probe ran against a local Archicad 29 model
(build 5101, Tapir 1.5.9) with a workspace folder added once via Library
Manager. Both halves hold:

- `ReloadLibraries` picked up a `.gsm` newly written into the linked folder,
  with no Library Manager interaction, and `CreateObjects` resolved it by name.
  The placed element rendered correctly and the transient probe deleted it
  again, leaving the project element count exactly where it started.
- A rebuild of the same object under the same name, followed by
  `ReloadLibraries` alone, updated an already-placed instance in place. The
  element was never re-placed and its own GUID was untouched; only the library
  part behind it changed, and the re-render showed the change. The library
  part's GUID stayed stable across the rebuild, which is what makes this work.

So the linked-library path in this design is verified, not assumed, and the
embedded-library fallback described below is a fallback rather than a
likelihood.

This is not ceremony. Four documented or header-evident API facts in this
project were false when actually run, and the one design step that skipped a
probe produced an elevation formula that would have put every railing a storey
too high. If the probe fails, the fallback is `AddFilesToEmbeddedLibrary` with
its known inability to overwrite, and the tool surface below barely changes:
`deploy_gdl_object` flips its default from linked to embedded and iteration
needs fresh names.

## Where the work lives

The `gdl` package stays a pure library. No MCP awareness enters `mesh.py`,
`generate.py`, `toolchain.py` or `deploy.py`. They already take paths and return
values, which is why they are reusable here at all. The CLI keeps working
unchanged; it remains the right tool on a machine with a local shell.

Three additions:

**`gdl/workspace.py`.** Resolves the configured workspace root and contains
every path inside it. One function turns a user-supplied name into an absolute
path or raises. All file-facing tool arguments go through it, so containment is
enforced in one place rather than re-checked per tool.

**`gdl/tools.py`.** The MCP adapter: registers the four tools, owns the config
argument schema, converts library exceptions into the codebase's
`{"error": ...}` shape. `build_server` calls
`_register_gdl_tools(mcp, default_port, workspace)` the way it already calls
`_register_full_mode_tools`.

This is a deliberate departure from `server.py` registering everything inline.
The config argument carries a large schema, and four tools plus that schema
would add roughly 200 lines to a file already at 600.

**A `gdl_workspace` field in `manifest.json`.** A directory `user_config` entry
exactly like the existing Office rules folder, arriving as
`ARCHICAD_MCP_GDL_WORKSPACE`. It gets a `resolve_gdl_workspace` twin of
`resolve_rules_dir`, because the same trap applies: an unfilled `.mcpb` field
arrives as `""`, and `Path("")` is `Path(".")`, which is truthy.

One refactor in `config.py`: `load_config` reads a file and parses in one pass.
Split out `parse_objects(raw: dict, base: Path)` so the file loader and the
inline tool argument share one parser. Without it the config schema exists twice
and drifts.

**Gating.** GDL tools are full mode only, alongside the other write tools.
Verdicts mode is the privacy-restricted surface and authoring library parts has
no place in it. An unset workspace means the tools do not register at all, and
the startup banner says so, matching how the rules folder already reports when
its field did not take.

## Tool surface

### `list_gdl_sources` (read-only)

What is in the workspace: source meshes, texture files, objects already defined
in `assets.json`, and built `.gsm` files, each with size and modified time.

This is the only way a sandboxed agent learns what the human dropped in, so it
is the natural first call.

### `inspect_gdl_source` (read-only)

Takes a source name relative to the workspace. Parses the mesh and returns
material groups with face counts, bounding box, detected units, and the parser's
notes (pivot correction applied, duplicate meshes dropped, vertices welded).

This is what the agent reads to compose a group map. Mirrors `archicad-gdl
inspect`. Runs with Archicad closed.

### `build_gdl_object` (destructive: writes files)

Arguments: `source`, `name`, `config` (inline, optional), `decimate`,
`validate`, `save_config`.

Runs load, optional Blender decimation, HSF generation, `hsf2libpart`, and
interpret validation. Writes `<name>.gsm` and `textures/` into the workspace
root, which **is** the linked library folder.

On success the config is merged into `assets.json` under `name`. Omitting
`config` rebuilds from what `assets.json` already holds, which is the iteration
path once a group map is settled.

Returns gsm path and size, A/B/H, GUID, the per-group summary, texture list, and
validation findings.

**Runs with Archicad closed**, since it needs only LP_XMLConverter. The operator
can reach a compiled object with no project open.

### `deploy_gdl_object` (destructive: mutates project)

Arguments: `name`, `place`, `keep`, `embed`, `port`.

Reloads libraries, places an instance, renders it via `GetElementPreviewImage`,
and returns the PNG inline as MCP image content (`fastmcp.utilities.types.Image`,
available in the pinned fastmcp 3.4.4) alongside the placed GUID and the load
result.

Returning the render inline is what makes the loop work at all. A clean
interpret run does not prove the geometry survived: Archicad silently drops
defective 3D bodies while every offline validator passes them, so looking at the
picture is the only automated gate. A sandboxed agent that cannot see the render
cannot verify its own work.

**The verification render defaults to a transient probe.** `keep=false` places
the instance, renders it, then deletes it, leaving the project net-zero. Without
this a conversational loop that rebuilds five times adjusting a group map leaves
five chairs stacked at the origin. `keep=true` is the explicit "actually place
it" path.

The internal delete does not require `confirm`, unlike `delete_elements`,
because it removes only the element the same call just created. Stated here so
it reads as a deliberate scope rather than a gap in the confirmation policy.

`embed=true` additionally pushes the `.gsm` and textures into the embedded
library first, for when the object must travel inside the `.pln`. It inherits
Tapir's inability to overwrite an existing embedded file, and the tool reports
that in plain terms rather than passing through the misleading "outputPath is
not a valid relative path".

## Config flow

`inspect_gdl_source` returns the material groups. The agent composes a config
object and passes it directly to `build_gdl_object`. On a successful build it is
written into `assets.json` under the object name, so a later rebuild only needs
the name.

Iteration is therefore one call per attempt, and the accumulated office
knowledge still lands in a file at rest. The alternative of a separate
write-config tool was rejected: every tweak would cost two calls and the agent
re-sends the whole config anyway.

The config schema mirrors `ObjectConfig` in `gdl/config.py`: `guid`, `textures`,
`variants`, `frame_variants`, `groups` (label, texture role, rgb, uv_rotate) and
`decimate`.

## Workspace containment

Every path argument is a name relative to the configured root. `workspace.py`
resolves it and refuses anything landing outside, naming the resolved root in
the error.

The alternative of accepting arbitrary absolute paths was rejected: it turns the
server into an arbitrary file reader on behalf of a cloud-hosted agent, and
every path would have to be typed correctly by hand into chat.

## Windows portability

Required, not optional, since the target operator is on Windows.

- `find_lp_xmlconverter` gains a Windows branch globbing
  `C:\Program Files\GRAPHISOFT\Archicad NN\LP_XMLConverter.exe`, picking the
  highest version the same way the macOS branch does. `LP_XMLCONVERTER` stays
  the first thing checked. Today the function globs `/Applications/Graphisoft`
  and nothing else, so it raises `ToolchainError` immediately on Windows.
- `find_blender` gains the equivalent for
  `Blender Foundation\Blender X.Y\blender.exe`. `BLENDER` stays the override.
- `_downscale` shells out to `sips` and swallows the failure, so Windows
  silently ships textures at full resolution. One raw veneer jpg in the test
  assets is 9.5 MB at 3000px, and the whole point of the texture rework was
  giving Enscape and Twinmotion real files to load. **Replace `sips` with
  Pillow** for identical behaviour on both platforms. Cost is roughly 3 MB in a
  self-contained bundle, against removing a silent platform-dependent
  difference.

## Errors

`ToolchainError` and workspace containment errors join the handled set, so they
surface as `{"error": ...}` rather than a stack trace.

Two get platform-aware text: a missing LP_XMLConverter names the environment
variable and the expected path shape for the running OS, and a missing Blender
names which groups asked for decimation.

## Testing

**Unit.** Workspace containment against traversal, absolute paths and symlinks.
`parse_objects` producing an identical `ObjectConfig` from a file and from the
inline argument. The `assets.json` merge preserving other objects. Both `find_*`
functions against a monkeypatched filesystem for macOS and Windows layouts.

**Tool-level.** Registration gated on workspace presence and on full mode.

**Live-marked.** Build a known small source, deploy with `keep=false`, assert
the project element count is unchanged.

## Out of scope

- Automating the one-time Library Manager step. There is no API to add a
  library, so it stays a documented per-machine prerequisite.
- Removing or changing the `archicad-gdl` CLI.
- Any change to how meshes are parsed, decimated or encoded into GDL.
