# Tapir installer (`archicad-mcp install-tapir`, `install_tapir`): Design

**Date:** 2026-10-19
**Status:** Decisions taken in brainstorming (19.10.2026); awaiting approval of this document. Next: the
implementation plan.
**Context:** `2026-10-19-tapir-command-triage.md` (in the main checkout, not yet committed) concluded that
Tapir cannot be dropped: its commands only exist because Tapir is C++ loaded inside Archicad. The
recommendation there was to keep Tapir and have the MCP install and version-check it. This is that work.

## Purpose

Nobody should have to find the Tapir releases page, pick the right one of fourteen assets, and work out
where Archicad keeps its add-ons. The MCP installs the Tapir build that matches each Archicad on the
machine, checks it byte for byte, never touches a Tapir that is already there without showing it and
asking, and says plainly when Archicad has to be restarted. When Tapir is missing or too old, every
message names the exact file and the command that fixes it.

Success means:

- On this Mac, `archicad-mcp install-tapir --archicad 30 --dry-run` reports that Archicad 30 has no
  Tapir, downloads `TapirAddOn_AC30_Mac.zip` (1.7.1) to a temporary folder, confirms its sha256 against
  the pinned value, and says it would install it into
  `/Applications/Graphisoft/Archicad 30/Add-Ons/Tapir/`, writing nothing.
- The same command without `--dry-run`, after a yes, leaves a Tapir that Archicad 30 loads on its next
  start (`list_instances` then reports Tapir 1.7.1). This real install happens only when Aleš says so.
- A run across all versions shows the Archicad 29 fork and the Archicad 27 copy as present and leaves
  them alone.
- A user of the `.mcpb` extension, who has no terminal command, can do the same from chat through the
  `install_tapir` tool.
- A tool that needs a newer Tapir refuses before sending anything, naming the version it needs and the
  version installed.

## User decisions (19.10.2026)

- **Our own installer, not a wrapper around Tapir's.** A small Python installer inside the MCP: a pinned
  asset, a sha256 check, the same `Add-Ons/Tapir/` layout as Tapir's own installer, and it shows and asks
  before touching any existing Tapir.
- **Pin a tested version: 1.7.1**, with the sha256 of every asset committed in the repository and bumped
  deliberately with MCP releases. A `--latest` flag opts into the newest release instead.
- **Download, do not bundle.** Assets come from the GitHub release at install time and are checked
  against the pinned hash. `--from-file` covers machines without GitHub access. The `.mcpb` stays the
  size it is and MCP releases stay independent of Tapir releases.
- **A non-writable Add-Ons folder:** on macOS, ask for an administrator password through the system
  dialog, as Tapir's own installer does. On Windows, stop before changing anything and print the one
  command to run from an administrator terminal, plus the manual steps to hand to IT. Nothing is ever
  half-installed.
- **Behaviour agreed in the playback:** a plan first and no write without a yes; an existing Tapir is
  never touched by default and, when replaced, is moved to a backup folder outside Add-Ons, never
  deleted; staged install with rollback; the MCP tool runs the same code, dry run by default; messages
  name the exact asset; Windows is built and unit-tested here but not run.

## Facts the design rests on

- **Release assets (1.7.1, published 2026-10-15).** `TapirAddOn_AC<25..30>_Mac.zip` (1.3 to 2.5 MB)
  and `TapirAddOn_AC<25..30>_Win.apx` (3.7 to 4.5 MB), plus `TapirInstaller_Mac.zip` (19 MB) and
  `TapirInstaller_Win.exe` (12 MB). The GitHub release API gives every asset a `size` and a
  `digest` (`sha256:...`). Download URL:
  `https://github.com/ENZYME-APD/tapir-archicad-automation/releases/download/<tag>/<asset>`. Release
  tags are bare versions (`1.7.1`); older ones are named `Tapir 1.5.8` but tagged `1.5.8`.
- **A Mac asset is a zip of one `TapirAddOn_AC<ver>_Mac.bundle`; a Windows asset is the `.apx` file
  itself.** The Mac bundles are universal (x86_64, arm64) and ad-hoc signed.
- **Tapir's own installer** (`archicad-addon/Installer/tapir_installer.py`, MIT, read 2026-10-19):
  - installs into `<Add-Ons>/Tapir/` (Mac: `ditto -x -k` of the zip; Windows: copy of the `.apx`);
  - before that it **deletes, without asking**, the `Tapir/` folder and any loose
    `TapirAddOn_*.(apx|bundle)` directly in Add-Ons ("otherwise Archicad would try to load the add-on
    twice");
  - always installs the latest release; there is no version option;
  - finds the Add-Ons folder by a list of localized names (`Add-Ons`, `Extensions` in FRA, `Dodatki`
    in POL and others), falling back to the folder that holds `XReadCfg.txt` up to two levels down;
  - on macOS finds installations by scanning `/Applications` and its direct subfolders for
    `Archicad <N>` folders; on Windows by the uninstall registry keys, the Graphisoft vendor key, and
    the Program Files folders;
  - on macOS falls back to `osascript ... with administrator privileges` on a permission error; on
    Windows the executable asks for elevation (UAC) itself;
  - checks a download by size and by its first bytes (a zip or a DLL, not a proxy's HTML page).
- **Tapir updates itself** through "Check for Updates..." inside Archicad. That update replaces exactly
  the file Archicad loaded, wherever it is. So installing into `Add-Ons/Tapir/` stays compatible with
  it, and the pin only fixes the version we install, not the version a user ends up on.
- **The Tapir version of an installed copy cannot be read from disk.** The bundle's `Info.plist`
  carries Archicad's version (`CFBundleVersion 29.0.0.3000`), not Tapir's. Only a running Archicad
  answers `GetAddOnVersion`. Offline, the best available check is a byte comparison with a known
  release.
- **This Mac (19.10.2026).** Archicad 26 (`Archicad 26 Apple Silicon`), 27, 28, 29 and 30 in
  `/Applications/Graphisoft/`, plus a non-installation folder `Archicad Monolith Migration Libraries
  INT`. Tapir sits loose in Add-Ons in Archicad 27 (an official build, 1.6.0 or 1.7.0: it has
  `DeleteEmbeddedLibraryItems` but not `UpdateHotlinks`) and Archicad 29 (Aleš's local fork build: it
  has `CreateAssociativeDimensionChainsOnSection`, which no release has). Archicad 26, 28 and 30 have
  none. The Archicad 30 `Add-Ons` folder is owned by `alesd` and writable. Archicad 30 also has
  Graphisoft's own `MCP Commands.bundle` and `MCP Server.bundle` in `Auto-Updated Add-Ons`.
- **Windows** installs Archicad under `C:\Program Files\Graphisoft\Archicad <N>`, which needs
  administrator rights to write. A running Archicad locks the `.apx` it loaded.
- **The gateway registry already knows when each Tapir command appeared** (`CommandInfo.version`, the
  "since" version Tapir stamps on each command), but only for the definitions it was synced from: 1.5.9
  on main. Most commands the curated tools call are 1.0.x to 1.5.8 (`GetSectionElements`);
  `GetDetailsOfElements`, `Get/SetGDLParametersOfElements` and `CreateObjects` are 1.5.7. The editing
  tools (`edit_property_definitions`, `edit_classifications`, `import_definitions`) call
  `UpdateClassificationItems`, `UpdateClassificationSystems`, `ImportPropertiesXml` and
  `ImportClassificationsXml`, which arrived in Tapir 1.6.0 and are **not in the 1.5.9 definitions**.
- **Fork-only commands.** `local_commands.json` overlays commands from Aleš's Tapir fork that no
  release has: `CreateRailings` (used by `create_elements`) and `GetStairBoundaries`, stamped "1.5.9".
  The pinned 1.7.1 does not have them.
- **Version numbers cannot judge a fork.** The Archicad 29 fork reports itself as Tapir 1.5.10 yet has
  the 1.6.0 editing commands (live-verified for the editing tools).
- **Existing code gates on command availability, not version numbers**
  (`ArchicadConnection.tapir_command_available`, `definition_edit.EDIT_MARKER`). That is what makes a
  fork with extra or renumbered commands work, and the per-tool checks below keep that approach.
- **Archicad keeps an Add-On Manager section in its preferences.** On this Mac,
  `~/Library/Preferences/com.graphisoft.AC-ARM 29.0.0 INT v1.plist` has a key `Add-On Manager` holding
  `Default Location` (`%DEFAULT%`) and a `Disable` list of full add-on URLs
  (`lan.null2:///Applications/Graphisoft/Archicad%2029/Add-Ons/...bundle`). No add-on added from another
  folder exists here, so the key such an entry sits under is not known yet. Tapir's older manual route
  (and the current message in `connection.py`) was to point Add-On Manager at a file anywhere.
- **Work in flight on `feature/create-all-elements`** (uncommitted in the main checkout, 19.10.2026)
  syncs the definitions to 1.7.0, rewrites `create_elements` for more element types with its own
  per-type check that names the Tapir version a command arrived in, and edits `server.py`.
- `httpx` is already a dependency. CI runs the tests on Ubuntu, macOS and Windows.

## How it behaves

### 1. Find the Archicad installations

For each installation: its Archicad version, its folder, its Add-Ons folder.

- **macOS:** scan `/Applications` and its direct subfolders for folders whose name matches
  `Archicad <N>` (case-insensitive, extra words allowed: `Archicad 26 Apple Silicon`). Keep a folder
  only if it contains an `Archicad*.app` and an Add-Ons folder. This drops the Monolith libraries folder.
- **Windows:** the same three sources Tapir's installer uses (uninstall registry keys, the vendor key,
  Program Files), deduplicated by folder; keep a folder only if it contains `Archicad.exe` and an
  Add-Ons folder.
- **Add-Ons folder:** the localized-name list first, then the `XReadCfg.txt` marker (at most two levels
  down, not following links), never inside the `.app`.
- Detection takes a root folder argument so tests run against a fake tree on any platform.
- If the detection approach is ported closely from `tapir_installer.py`, the module carries Enzyme
  APD's MIT copyright notice in its header.

### 2. Read what is already there

For each installation, list every Tapir entry: the `Tapir/` folder (each entry inside it), and every
loose `TapirAddOn_*.(bundle|apx)` directly in Add-Ons. For each entry show its path, its modified date,
its size, and whether it is **identical to the pinned release**:

- Windows: sha256 of the `.apx` against the asset's sha256.
- macOS: sha256 of the bundle's main executable (`Contents/MacOS/<CFBundleExecutable>`) against the
  same file inside the downloaded zip. Without the download (offline dry run) the answer is "unknown".

If an Archicad of that version is running and answers (a bounded probe, best effort), also show the
Tapir version it reports. Flag a loose bundle as "loose in Add-Ons" and both a loose bundle and a
`Tapir/` folder as "two copies, Archicad may load Tapir twice".

Tapir can also be loaded from outside Add-Ons, through Add-On Manager. Two best-effort checks catch
that (my call):

- **Add-On Manager preferences.** macOS: every `com.graphisoft.AC*<N>.0.0*.plist` in
  `~/Library/Preferences` (the `-ARM` part and the language and release suffix vary); Windows: the
  matching `HKCU\Software\Graphisoft\...` key (not verified). Any string in the `Add-On Manager`
  section that names a `TapirAddOn_` file outside this Add-Ons folder counts as a Tapir present at
  that path; one in the `Disable` list is shown as "disabled in Add-On Manager". A `Default Location`
  other than `%DEFAULT%` is reported, since Archicad may then load add-ons from that folder too.
- **A running Archicad** of that version that reports Tapir while nothing is found on disk is shown as
  "Tapir loaded from somewhere this installer cannot see".

Either one puts the installation in the "different Tapir present" state, so it is skipped by default.
For a row in "no Tapir" with no running instance to ask, the plan adds one line: if Tapir was added
through Add-On Manager from another folder, installing gives Archicad two copies.

Each installation ends up in one state: **no Tapir**, **up to date** (identical to the pin),
**different Tapir present**, or **no build available** (the pinned release has no asset for that
Archicad version or platform).

### 3. Pinned release data

`src/archicad_mcp/tapir_install/tapir_release.json`, committed:

```json
{
  "version": "1.7.1",
  "tag": "1.7.1",
  "published": "2026-10-15",
  "assets": {
    "TapirAddOn_AC30_Mac.zip": {"size": 2498912, "sha256": "a08e6304..."},
    "TapirAddOn_AC30_Win.apx": {"size": 4413440, "sha256": "42c3f585..."}
  }
}
```

with all twelve add-on assets (not the installers). `scripts/pin_tapir_release.py <tag>` writes it
from the GitHub release API (`size` and `digest`), so a bump is one command plus a review of the diff.
Asset name for a version and platform: `TapirAddOn_AC<ver>_Mac.zip` or `TapirAddOn_AC<ver>_Win.apx`.

`--latest` reads the latest release from the API instead and checks each asset against the `digest`
GitHub publishes for it. If the API is unreachable or gives no digest, `--latest` refuses rather than
installing an unchecked file, and says the pinned version still works.

### 4. Download and check

- One fresh temporary folder per run; the asset downloaded into it with `httpx`, following redirects,
  with a timeout and a `User-Agent` naming the MCP and its version, refusing anything larger than the
  expected size.
- Refuse unless the size and the sha256 match. A mismatch names both hashes and suggests a proxy or a
  changed release; nothing is installed.
- Mac zip layout, checked before extracting: every member under one top-level
  `TapirAddOn_AC<ver>_Mac.bundle/`, no absolute paths, no `..`, a bounded total uncompressed size.
- `--from-file PATH` (needs exactly one `--archicad`) skips the download. The file's name must be that
  version's asset name and it is held to the same size and sha256.
- The download is untrusted data: nothing from it is run; it is only checked, extracted and moved.
- The temporary folder is removed at the end, also on failure.
- **Dry runs download and check too** (that is what proves the hash and the network path), and never
  write outside the temporary folder.

### 5. The plan and the yes

The plan is a short table, one row per installation: Archicad version, Add-Ons folder, what is there
(section 2), and the action:

| State | Default action | With `--replace` / `replace_existing=true` |
|---|---|---|
| No Tapir | install | install |
| Up to date | nothing ("already 1.7.1") | nothing |
| Different Tapir present | skip, and say how to replace | replace, keeping a backup |
| No build available | skip, and say why (and that `--latest` may have one) | skip |

`--archicad N` (repeatable) narrows the rows; without it every detected installation is listed.
A writable check (create and delete a probe file in Add-Ons) adds "needs an administrator password"
(macOS) or "needs an administrator terminal" (Windows) to a row.

**CLI.** After the plan, in an interactive terminal: one prompt for the installs ("Install Tapir 1.7.1
into Archicad 26, 28, 30? [y/N]"), then one prompt per replacement showing what is there and where the
backup goes. `--yes` answers yes to the prompts the flags allow; it never implies `--replace`. Without a
terminal and without `--yes`, it prints the plan, writes nothing, and says to pass `--yes`.

**MCP tool.** Without `confirm=true` it returns the plan. With `confirm=true` it carries it out; the
client's own approval of the tool call is the human yes.

### 6. Install, per Archicad

1. Stage: Mac, `ditto -x -k` the checked zip into a fresh staging folder (keeps the bundle's
   permissions and links, which `zipfile` drops) and confirm the bundle's `Info.plist` names an
   executable that exists. Windows: the checked `.apx` is the staged file.
2. Replacing only: move every existing Tapir entry of that installation (the `Tapir/` folder and loose
   bundles) into the backup folder,
   `~/Library/Application Support/archicad-mcp/tapir-backups/AC<ver>-<YYYYMMDD-HHMMSS>/` on macOS and
   `%LOCALAPPDATA%\archicad-mcp\tapir-backups\...` on Windows, with a `backup.json` naming where each
   entry came from. The backup is outside Add-Ons so Archicad cannot load it, and it stays owned by the
   user even when the move ran as administrator.
3. Move the staged bundle or `.apx` into `<Add-Ons>/Tapir/` (created if missing).
4. Any failure after step 2 moves the backed-up entries back and removes what step 3 placed. The result
   says whether the rollback succeeded.
5. Report what was installed, where, the backup folder (with the one-line way to restore it), and
   "Restart Archicad <N> to load Tapir" (or "Archicad <N> is running: quit and reopen it" when a running
   instance of that version answered).

An existing Tapir is **never deleted** by this code, only moved to a backup.

### 7. When Add-Ons is not writable

- **macOS:** steps 2 and 3 run as one `osascript -e 'do shell script "..." with administrator
  privileges'`, built only from `shlex`-quoted fixed paths (staging folder, Add-Ons, backup folder),
  never from free text. If the user cancels the dialog, nothing has changed and the result says so.
  The tool's dry run warns that the dialog will appear.
- **Windows:** stop before step 2 and print the exact command for an administrator terminal, using the
  running interpreter so it also works for the `.mcpb` runtime:
  `"<sys.executable>" -I -m archicad_mcp.tapir_install --archicad 29` (or `archicad-mcp install-tapir
  --archicad 29` when installed as a uv tool), followed by the manual steps for IT: the asset name, its
  sha256, the release URL, the target folder. A locked `.apx` while replacing (Archicad running) is
  reported as "close Archicad <N> and retry", after rollback.
- No automatic UAC elevation on Windows in this version.

## Entry points

**CLI:** `archicad-mcp install-tapir [--archicad N]... [--dry-run] [--replace] [--yes]
[--from-file PATH] [--latest] [--add-ons-folder PATH]`. `main()` in `server.py` hands off to the
installer when the first argument is `install-tapir`, before the server's own argument parsing. The
same CLI runs as `python -m archicad_mcp.tapir_install`. `--add-ons-folder` (with exactly one
`--archicad`) is for an installation detection does not find.

**MCP tool:** `install_tapir(archicad_version: int | None = None, replace_existing: bool = False,
confirm: bool = False)`. Full mode only; it needs no running Archicad. Returns per installation the
version, Add-Ons folder, what was there, the action, the check result and the outcome, plus
`restart_required` and the backup folders. Description, roughly: "Install the pinned Tapir add-on into
Archicad's Add-Ons folder. Dry run by default; confirm=true writes. Never replaces an existing Tapir
unless replace_existing=true, and then moves the old copy to a backup folder. Archicad must be
restarted afterwards." Listed in `manifest.json`.

## Missing and outdated Tapir messages

One helper builds the fix-it text for an Archicad version and platform, used everywhere below: the
asset name (`TapirAddOn_AC30_Mac.zip`), the pinned release URL
(`.../releases/tag/1.7.1`), the target folder when detection finds one, and both ways to install
("run `archicad-mcp install-tapir --archicad 30`, or call the `install_tapir` tool").

- **`ArchicadConnection.tapir()` when Tapir is missing** (connection.py): names the Archicad version and
  port and appends the helper text. The connection learns its Archicad version lazily from
  `API.GetProductInfo` (cached), so callers do not change.
- **Startup banner** (`_instance_line`): the "not installed" line ends with the install command.
- **`list_instances`**: an instance without Tapir gets a `tapir_hint` field with the helper text. An
  instance with Tapir is judged by **command probes, never by its version number** (my call): it gets
  `tools_needing_newer_tapir`, the gated tools whose probe command is absent (one probe per gated tool,
  only on instances that answered discovery, so a handful of local calls). When that list is not
  empty, `tapir_hint` adds that replacing a custom build with the pinned release loses the build's own
  commands. A fork like the Archicad 29 one (reports 1.5.10, has the 1.6.0 commands) is therefore not
  called outdated.
- **Existing degrade notes** that say "install or update the Tapir add-on" (`extract.py`,
  `definitions.py`, `definition_edit.py`) gain the same text where they have a connection to ask.

## Per-tool minimum Tapir versions

`src/archicad_mcp/tapir_requirements.py`:

- A table of the curated tools that cannot work without Tapir, each with the Tapir commands **every
  call** needs (not the ones it degrades around, not the ones only some arguments need).
- **Where a command's version comes from** (my call): the "since" version in the gateway registry. A
  command the synced definitions do not have yet (the 1.6.0 editing commands on main) carries an
  explicit version with a reason in the table. A test requires one or the other, and fails when an
  explicit version disagrees with the registry once the definitions are synced, so the explicit entries
  get removed then. This branch does not re-sync the definitions: `feature/create-all-elements` syncs
  them to 1.7.0, and two syncs of the generated files would collide.
- A tool's minimum version is the highest of its commands' versions. An override with a written reason
  is allowed for a behaviour fix a tool depends on (the builder checks whether `create_swept_beam`
  depends on the object-size fix, Tapir PR #718, and which release carried it).
- **Fork-only commands** (from `local_commands.json`, in no release) have no minimum version (my
  call). A tool or argument that needs one refuses with "needs a Tapir build with `CreateRailings`,
  which no Tapir release has yet". They are left out of the test that the pinned version meets every
  minimum.
- The gate probes **one** command per tool, the one that sets its minimum, through
  `tapir_command_available`, so a fork that has the commands passes whatever version it reports. If
  Tapir is missing: the missing message. If the command is absent: "`edit_classifications` needs Tapir
  1.6.0 or newer (this Archicad has 1.5.9)", plus the helper text. Nothing is sent to Archicad.
- **Commands that depend on the arguments** go through the same helper,
  `require_tapir_command(conn, command, tool)`, called where the command is chosen, so a column still
  works on a Tapir too old for objects. `create_elements` is the case in point, and it is **left out of
  this branch** (my call): `feature/create-all-elements` adds its own per-type check there. A follow-up
  points that check at the shared helper once both have merged.
- Tools that degrade without Tapir (`find_elements`, `get_selection` and the like) are not gated.

## Files

- `src/archicad_mcp/tapir_install/`: `__init__.py`, `__main__.py`, `detect.py`, `release.py`,
  `install.py`, `cli.py`, `tapir_release.json` (the split is the builder's).
- `src/archicad_mcp/tapir_requirements.py`.
- `src/archicad_mcp/connection.py`, `src/archicad_mcp/server.py` (dispatch, tool, gates, banner,
  `list_instances`), `extract.py`, `core/definitions.py`, `core/definition_edit.py` (messages).
- `scripts/pin_tapir_release.py`.
- `manifest.json` (the tool), `README.md`.
- `tests/`.

## Tests

No test touches the network or the real `/Applications` or Program Files.

- Detection on fake trees: the Mac layout of this machine (including `Archicad 26 Apple Silicon` and the
  Monolith folder), a Windows layout, a localized `Dodatki` folder, the `XReadCfg.txt` fallback.
- Existing state: none, `Tapir/` folder, loose bundle, both; identical and different against a fake
  release.
- Asset names per version and platform; no build for Archicad 31.
- Checks: wrong sha256, wrong size, an HTML page, a zip with `..`, a zip with two top-level entries, a
  wrong file name for `--from-file`; each refuses and leaves the tree unchanged.
- Plan: defaults (install only where missing), `--replace` gating, up to date, `--archicad` narrowing.
- Install on a fake tree: fresh install; replace with backup and `backup.json`; an injected failure
  after the backup rolls back. Mac extraction with `ditto` runs on the macOS CI runner; the Windows
  copy path runs everywhere.
- Not writable: a read-only fake Add-Ons gives the Windows command and steps, and on macOS calls a fake
  elevation runner with the expected quoted paths.
- CLI: `--dry-run` and a non-interactive run without `--yes` write nothing.
- Tool: not registered in verdicts mode; `confirm=false` writes nothing.
- Messages: the missing message names `TapirAddOn_AC30_Mac.zip` on macOS and `.apx` on Windows, with
  the pinned tag.
- Requirements: every tool in the table is registered; every command has a version from the registry
  or an explicit one with a reason, and an explicit one agrees with the registry where the registry has
  the command; the probed command sets the minimum; the pinned version is at least every release-backed
  tool minimum and at least the synced definitions version (`tapir_version.json`); the gate refuses
  with both version numbers on a fake connection, and with the "no release yet" text for a fork-only
  command.
- Detection reads a fake Add-On Manager plist naming a `TapirAddOn_` outside Add-Ons, and one in the
  `Disable` list.
- Downloads go through an `httpx.MockTransport`.

## Acceptance on this Mac

1. `uv run archicad-mcp install-tapir --dry-run`: lists Archicad 26 to 30; 27 and 29 shown as
   "different Tapir present" (29 loose, the fork) and skipped; 26, 28 and 30 as installs; nothing
   written.
2. `uv run archicad-mcp install-tapir --archicad 30 --dry-run`: the result described under Purpose.
3. Only on Aleš's go-ahead: the real Archicad 30 install, an Archicad 30 restart, and `list_instances`
   reporting Tapir 1.7.1. Until that happens the pin is "pinned, not yet live-tested in the MCP", and
   the README says which version was exercised live.

## README

- Requirements: Tapir becomes "installed for you by `archicad-mcp install-tapir` or the `install_tapir`
  tool", with the pinned version.
- A new "Install Tapir" section: what the command does, the dry run, the flags, what happens to an
  existing Tapir and where backups go, the macOS password dialog, the Windows administrator terminal,
  the manual route (asset name pattern, `Add-Ons/Tapir/`, restart).
- "Check it works": the banner and `list_instances` hint.

## Out of scope

- Uninstall and restore commands (the result prints how to restore by hand).
- Shipping Tapir inside the `.mcpb`.
- Installing or updating Tapir automatically at server start.
- Automatic UAC elevation on Windows; running the Windows path on a real machine (unit tests only).
- Linux.
- Graphisoft's `MCP Commands` add-on.

## Branch

Built on `main` in its own branch (this worktree). `server.py` changes stay in their own functions
where possible, because `feature/create-all-elements` edits the same file; whichever lands second
resolves the conflict.

## Left to the builder

Module split, table formatting, exact wording within the rules above, exit codes, how the running
Archicad probe is bounded, and whether to read the OS trust store (the `truststore` package) for
office proxies that re-sign TLS. That last one adds a dependency, so it is noted in the result.
