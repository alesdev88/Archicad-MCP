# Tapir installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `archicad-mcp install-tapir` and an `install_tapir` MCP tool that put the pinned Tapir build into each Archicad's `Add-Ons/Tapir/`, checked by sha256, never touching an existing Tapir without a yes; plus missing/outdated Tapir messages that name the exact asset, and per-tool Tapir requirements.

**Architecture:** A new package `archicad_mcp.tapir_install` (release data and download, detection, plan, install, CLI, hint text) that knows nothing about the MCP server, and a module `archicad_mcp.tapir_requirements` that maps tools to the Tapir commands they need. `server.py` dispatches the CLI, registers the tool, and calls the gates; `connection.py` and the banner use the hint text.

**Tech Stack:** Python 3.12, httpx (already a dependency), `ditto` and `osascript` on macOS, `winreg` on Windows, pytest with `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-10-19-tapir-installer-design.md`

## Global Constraints

- Pinned Tapir release: tag `1.7.1`, published 2026-10-15, the twelve `TapirAddOn_AC<25..30>_<Mac.zip|Win.apx>` assets with their GitHub `size` and `sha256`.
- Install target: `<Add-Ons>/Tapir/TapirAddOn_AC<ver>_Mac.bundle` (macOS) or `<Add-Ons>/Tapir/TapirAddOn_AC<ver>_Win.apx` (Windows).
- An existing Tapir is never deleted; replacing needs `--replace` / `replace_existing=true` and moves it to `~/Library/Application Support/archicad-mcp/tapir-backups/AC<ver>-<YYYYMMDD-HHMMSS>/` (macOS) or `%LOCALAPPDATA%\archicad-mcp\tapir-backups\AC<ver>-<YYYYMMDD-HHMMSS>\` (Windows), with `backup.json`.
- Dry runs download and check, and write nothing outside their temporary folder.
- No test touches the network, `/Applications`, Program Files, or the real Preferences.
- No em or en dashes in any text: code, comments, docs, commit messages.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not re-sync the gateway definitions in this branch; do not change `create_elements` or `core/create.py`.

## Review Focus

1. **A partially failed replace.** The backup moved, the new copy failed to land: the user expects the old Tapir back where it was. Pinned by `test_install_rolls_back_when_the_move_in_fails` (Task 5).
2. **An Add-Ons folder that already holds an unrelated file named `Tapir`** (a file, not a folder): the installer must refuse that row, not crash or overwrite. Pinned by `test_plan_refuses_when_tapir_is_a_file` (Task 4).
3. **A GitHub redirect to a proxy or login page** returning 200 with HTML: must be refused before anything is extracted. Pinned by `test_download_refuses_html_page` (Task 2).
4. **Two installations of the same Archicad version** (an INT and a localized copy): each gets its own row; `--archicad 30` selects both. Pinned by `test_find_installations_keeps_two_copies_of_one_version` (Task 3).
5. **The CLI run with stdin closed** (piped from a script, `--yes` forgotten): plan printed, nothing written, exit code 2. Pinned by `test_cli_without_terminal_or_yes_writes_nothing` (Task 6).

---

### Task 1: Pinned release data and asset names

**Files:**
- Create: `src/archicad_mcp/tapir_install/__init__.py`, `src/archicad_mcp/tapir_install/release.py`, `src/archicad_mcp/tapir_install/tapir_release.json`
- Create: `scripts/pin_tapir_release.py`
- Test: `tests/test_tapir_release.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Asset: name: str; size: int; sha256: str; url: str`
  - `@dataclass(frozen=True) class Release: version: str; tag: str; assets: dict[str, Asset]; pinned: bool`
  - `RELEASES_URL = "https://github.com/ENZYME-APD/tapir-archicad-automation/releases"`
  - `load_pinned() -> Release` (reads `tapir_release.json` next to the module; `url` = `f"{RELEASES_URL}/download/{tag}/{name}"`)
  - `platform_key(platform: str | None = None) -> str` returning `"Mac"` for `darwin`, `"Win"` for `win32`, `ValueError` otherwise (`platform` defaults to `sys.platform`)
  - `asset_name(archicad_version: int, platform: str | None = None) -> str` (`TapirAddOn_AC30_Mac.zip`, `TapirAddOn_AC30_Win.apx`)
  - `release_page_url(release: Release) -> str` (`f"{RELEASES_URL}/tag/{release.tag}"`)
  - `fetch_latest(client: httpx.Client) -> Release` (GitHub API `releases/latest`; each asset's `digest` must be `sha256:<hex>`, else `InstallError("... --latest needs GitHub's sha256 digest ...; the pinned version still works")`; `pinned=False`)
  - `class InstallError(Exception)` (user-facing message in `str()`), defined in `release.py` and re-exported from `__init__.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_pinned_release_is_1_7_1_with_all_twelve_addon_assets():
    rel = load_pinned()
    assert rel.version == "1.7.1" and rel.tag == "1.7.1" and rel.pinned
    assert set(rel.assets) == {f"TapirAddOn_AC{v}_{p}" for v in range(25, 31)
                               for p in ("Mac.zip", "Win.apx")}
    a = rel.assets["TapirAddOn_AC30_Mac.zip"]
    assert a.size == 2498912
    assert a.sha256 == "a08e63048c705c3c99a26b51dba1beb485f762c3f4d5f53d75423b29daee254f"
    assert a.url.endswith("/releases/download/1.7.1/TapirAddOn_AC30_Mac.zip")

def test_asset_name_per_platform():
    assert asset_name(30, "darwin") == "TapirAddOn_AC30_Mac.zip"
    assert asset_name(29, "win32") == "TapirAddOn_AC29_Win.apx"
    with pytest.raises(ValueError):
        asset_name(29, "linux")

def test_fetch_latest_refuses_assets_without_digest():  # MockTransport serving a release JSON lacking "digest"
    ...raises InstallError, message contains "--latest" and "pinned"

def test_fetch_latest_reads_digests():  # MockTransport; asset digest "sha256:ab..." -> Asset.sha256 == "ab..."
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tapir_release.py -v`; expected: FAIL (module missing).
- [ ] **Step 3: Write `scripts/pin_tapir_release.py <tag>`**: `gh`-free, uses httpx against `https://api.github.com/repos/ENZYME-APD/tapir-archicad-automation/releases/tags/<tag>`, keeps only `TapirAddOn_*` assets, writes `{"version", "tag", "published", "assets": {name: {"size", "sha256"}}}` sorted, 2-space indent. Run it for `1.7.1` to produce `tapir_release.json` (network allowed here; this is the one real fetch).
- [ ] **Step 4: Implement `release.py`** per the Interfaces block.
- [ ] **Step 5: Run the tests**; expected: PASS. Spot-check the JSON's AC30 Mac sha256 against the value in the test.
- [ ] **Step 6: Commit** `feat: pinned Tapir 1.7.1 release data and asset names`.

### Task 2: Download and check

**Files:**
- Modify: `src/archicad_mcp/tapir_install/release.py`
- Test: `tests/test_tapir_release.py`

**Interfaces:**
- Produces:
  - `download(asset: Asset, dest_dir: Path, client: httpx.Client) -> Path` (streams to `dest_dir/asset.name`, refuses more bytes than `asset.size`, follows redirects; `User-Agent: archicad-mcp/<package version>`)
  - `verify(path: Path, asset: Asset) -> None` (size, then sha256; mismatch raises `InstallError` naming expected and actual sha256 and suggesting a proxy or a changed release)
  - `check_mac_zip(path: Path, archicad_version: int) -> str` (returns the bundle folder name; raises `InstallError` unless every member is under exactly one top-level `TapirAddOn_AC<ver>_Mac.bundle/`, no absolute path, no `..`, total uncompressed size at most 200 MB)
  - `make_client() -> httpx.Client` (timeout 60 s; tests pass their own client)

- [ ] **Step 1: Write the failing tests** (fake assets built in `tmp_path`; `Asset` values computed from the fake bytes):
  - `test_download_and_verify_accepts_matching_file`
  - `test_verify_refuses_wrong_sha256`: message contains both hashes.
  - `test_download_refuses_more_bytes_than_expected`
  - `test_download_refuses_html_page`: MockTransport returns `200`, `<html>...</html>` of the right size claimed; `verify` raises (hash).
  - `test_check_mac_zip_accepts_one_bundle`, `test_check_mac_zip_refuses_dotdot`, `test_check_mac_zip_refuses_two_top_level_entries`, `test_check_mac_zip_refuses_wrong_version_bundle`.
- [ ] **Step 2: Run**, expected FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run**, expected PASS.
- [ ] **Step 5: Commit** `feat: download and check Tapir assets`.

### Task 3: Find installations and existing Tapir

**Files:**
- Create: `src/archicad_mcp/tapir_install/detect.py`
- Test: `tests/test_tapir_detect.py`

**Interfaces:**
- Produces:
  - `@dataclass class Installation: version: int; folder: Path; add_ons: Path`
  - `@dataclass class TapirEntry: path: Path; kind: str  # "tapir-folder" | "loose" | "add-on-manager" | "disabled"; modified: datetime | None; size: int | None`
  - `LOCALIZED_ADDONS_FOLDER_NAMES: tuple[str, ...]` (copied from Tapir's installer, with the MIT notice in the module header)
  - `find_installations(platform: str | None = None, *, root: Path | None = None, registry_paths: list[Path] | None = None) -> list[Installation]` (macOS: scan `root or /Applications` and its direct subfolders for `Archicad <N>` folders holding an `Archicad*.app` and an Add-Ons folder; Windows: `root` given means scan it like Program Files; otherwise registry uninstall keys, the `HKLM\SOFTWARE\GRAPHISOFT` vendor key and `%ProgramFiles%`, folders holding `Archicad.exe`; sorted by version then folder)
  - `find_add_ons_folder(install_folder: Path) -> Path | None` (localized names first, NFC-casefold compare, then `XReadCfg.txt` at most two levels down, no links, never inside `*.app`)
  - `tapir_entries(inst: Installation, *, prefs_dir: Path | None = None) -> list[TapirEntry]` (the `Tapir/` folder's entries, loose `TapirAddOn_*.(bundle|apx)` in Add-Ons, and on macOS `TapirAddOn_` strings in the `Add-On Manager` dict of `com.graphisoft.AC*<ver>.0.0*.plist` under `prefs_dir or ~/Library/Preferences`, read with `plistlib`; a `lan.null2:///` or `file://` URL is decoded to a path; entries inside this Add-Ons folder are not repeated; one under `Disable` gets kind `"disabled"`)
  - `default_location(inst: Installation, *, prefs_dir: Path | None = None) -> str | None` (the `Default Location` value when it is not `%DEFAULT%`)
  - `main_binary(entry_path: Path) -> Path | None` (bundle: `Contents/MacOS/<CFBundleExecutable>` from `Info.plist`; `.apx`: itself)

- [ ] **Step 1: Write the failing tests** with fake trees in `tmp_path` (a helper builds `Archicad 30/Archicad 30.app/`, `Archicad 30/Add-Ons/XReadCfg.txt`):
  - `test_find_installations_mac_layout_of_this_machine`: folders `Graphisoft/Archicad 26 Apple Silicon`, `Graphisoft/Archicad 27`, `Graphisoft/Archicad 30`, `Graphisoft/Archicad Monolith Migration Libraries INT` (no app) give versions `[26, 27, 30]`.
  - `test_find_installations_keeps_two_copies_of_one_version`: `Archicad 30` and `Archicad 30 GER` both returned.
  - `test_add_ons_folder_localized_name`: `Dodatki` found; `test_add_ons_folder_by_marker`: an unknown name holding `Sub/XReadCfg.txt` found.
  - `test_find_installations_windows_layout`: `root` with `Archicad 29/Archicad.exe` and `Add-Ons/`, `platform="win32"`.
  - `test_tapir_entries_folder_loose_and_both`
  - `test_tapir_entries_reads_add_on_manager_plist`: a plist written with `plistlib` whose `Add-On Manager` holds `{"Additional": {"#1.": "lan.null2:///Users/x/Tapir/TapirAddOn_AC30_Mac.bundle"}, "Disable": {"#1.": "lan.null2:///Applications/Other/TapirAddOn_AC30_Mac.bundle", "Disable Number": 1}}` gives one `"add-on-manager"` and one `"disabled"` entry, path `/Users/x/Tapir/TapirAddOn_AC30_Mac.bundle`.
  - `test_default_location_reported_only_when_not_default`
- [ ] **Step 2: Run**, expected FAIL.
- [ ] **Step 3: Implement** per Interfaces. Windows registry code is behind `if platform == "win32"` with `import winreg` inside, so it never runs elsewhere.
- [ ] **Step 4: Run**, expected PASS.
- [ ] **Step 5: Commit** `feat: find Archicad installations and existing Tapir copies`.

### Task 4: The plan

**Files:**
- Create: `src/archicad_mcp/tapir_install/plan.py`
- Test: `tests/test_tapir_plan.py`

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces:
  - States: `NO_TAPIR = "no Tapir"`, `UP_TO_DATE = "up to date"`, `DIFFERENT = "different Tapir present"`, `NO_BUILD = "no build available"`, `BLOCKED = "blocked"`
  - Actions: `"install"`, `"replace"`, `"nothing"`, `"skip"`
  - `@dataclass class Row: inst: Installation; asset: Asset | None; entries: list[TapirEntry]; identical: dict[Path, bool | None]; state: str; action: str; writable: bool; running_version: str | None; notes: list[str]`
  - `is_writable(folder: Path) -> bool` (create and delete `.archicad-mcp-write-test-<pid>`)
  - `build_plan(installations: list[Installation], release: Release, *, versions: set[int] | None, replace: bool, reference: dict[int, Path] | None = None, running: dict[int, str] | None = None, prefs_dir: Path | None = None, platform: str | None = None) -> list[Row]`
    - `reference` maps an Archicad version to the checked downloaded asset; it is used for `identical` (sha256 of `main_binary` of each entry against the same member of the zip, or of the `.apx` against `asset.sha256`); without it `identical` is `None` ("unknown").
    - `running` maps an Archicad version to the Tapir version a running instance reported; a version present there while no entry exists adds a `"running"` pseudo-entry and makes the row `DIFFERENT`.
    - state `UP_TO_DATE` only when every entry is identical; `DIFFERENT` for any other entry; `NO_BUILD` when the release has no asset; `BLOCKED` with a note when `<Add-Ons>/Tapir` exists and is not a folder.
    - action from the spec's table (section 5); notes include "loose in Add-Ons", "two copies, Archicad may load Tapir twice", "needs an administrator password" (macOS, not writable), "needs an administrator terminal" (Windows, not writable), the Add-On Manager warning for `NO_TAPIR` rows without a running instance, and a non-default `Default Location`.
  - `running_tapir_versions() -> dict[int, str]` (from `connection.discover_instances()`, best effort, any exception gives `{}`)

- [ ] **Step 1: Write the failing tests**:
  - `test_plan_installs_where_missing_and_skips_others`: three fake installs (none, loose different bundle, identical `Tapir/` copy) give actions `install`, `skip`, `nothing` and states `NO_TAPIR`, `DIFFERENT`, `UP_TO_DATE`.
  - `test_plan_replace_turns_skip_into_replace`
  - `test_plan_versions_narrows_rows`
  - `test_plan_no_build_for_archicad_31`
  - `test_plan_without_reference_says_unknown`: `identical[path] is None`, state `DIFFERENT`.
  - `test_plan_running_tapir_without_files_is_different`
  - `test_plan_refuses_when_tapir_is_a_file`: state `BLOCKED`, action `skip`.
  - `test_plan_not_writable_note` (skip on Windows and when running as root): `chmod 0o555` on the fake Add-Ons gives `writable=False` and the macOS note.
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement.** **Step 4: Run**, PASS.
- [ ] **Step 5: Commit** `feat: Tapir install plan per Archicad installation`.

### Task 5: Install, backup, rollback, elevation

**Files:**
- Create: `src/archicad_mcp/tapir_install/install.py`
- Test: `tests/test_tapir_install.py`

**Interfaces:**
- Consumes: `Row`, `Asset`, `check_mac_zip`.
- Produces:
  - `backup_root(platform: str | None = None) -> Path`
  - `stage(downloaded: Path, row: Row, work_dir: Path, *, platform: str | None = None) -> Path` (macOS: `ditto -x -k` into `work_dir/stage-AC<ver>/`, then the bundle's `Info.plist` must name an executable that exists; Windows: returns `downloaded`)
  - `@dataclass class Outcome: version: int; installed: Path | None; backup: Path | None; restored: bool | None; error: str | None; restart_note: str`
  - `apply_row(row: Row, staged: Path, *, platform: str | None = None, backups: Path | None = None, now: datetime | None = None, elevate: Callable[[list[list[str]]], None] | None = None) -> Outcome`
    - replace: move every file-system entry (kinds `"tapir-folder"` and `"loose"`, never Add-On Manager or running entries) to `backups/AC<ver>-<stamp>/<name>` and write `backup.json` `{"archicad": ver, "created": iso, "mcp_version": ..., "entries": [{"from": str, "to": str}]}`; then move `staged` into `<Add-Ons>/Tapir/` (creating it); on failure after the backup, move entries back and remove what was placed; `restored` says whether that worked.
    - not writable on macOS: build the same steps as argv lists (`["mkdir", "-p", ...]`, `["mv", src, dst]`, `["chown", "-R", f"{uid}", backup_dir]`) and pass them to `elevate`; the default `elevate` joins them with `shlex.join` and ` && ` into one `osascript -e 'do shell script "..." with administrator privileges'`; a cancelled dialog (osascript exit 1 with `User canceled`) gives `error="cancelled at the administrator password dialog; nothing changed"`.
    - not writable on Windows: no change; `error` holds `windows_admin_text(row)`.
    - a `PermissionError` while moving a loaded `.apx` on Windows: roll back, `error="close Archicad <N> and retry"`.
  - `windows_admin_text(row: Row, *, executable: str | None = None, uv_tool: bool | None = None) -> str` (the administrator terminal command `"<sys.executable>" -I -m archicad_mcp.tapir_install --archicad <ver>`, or `archicad-mcp install-tapir --archicad <ver>` when `uv_tool`, then the IT steps: asset name, sha256, release page, target folder)
  - `restart_note` is `"Restart Archicad <N> to load Tapir."` or, with `row.running_version` set, `"Archicad <N> is running: quit and reopen it to load Tapir."`

- [ ] **Step 1: Write the failing tests** (fake trees; Windows-style rows use a fake `.apx` and run on every OS; the `ditto` test is `skipif(sys.platform != "darwin")`):
  - `test_fresh_install_windows_copy`: `.apx` lands in `Add-Ons/Tapir/`, `backup is None`.
  - `test_fresh_install_mac_ditto` (darwin only): a zip built with `ditto -c -k --keepParent` of a fake bundle is staged and installed; executable bit kept.
  - `test_replace_moves_old_copies_to_backup_with_manifest`: loose bundle and `Tapir/` both end up under `backups/AC29-20261019-120000/`, `backup.json` lists both, nothing left in Add-Ons but the new `Tapir/`.
  - `test_install_rolls_back_when_the_move_in_fails`: monkeypatch the final move to raise `OSError`; old entries are back at their original paths, `restored is True`, `error` mentions the failure.
  - `test_mac_not_writable_uses_elevation_runner`: fake `elevate` captures the argv lists; they quote paths with spaces correctly and end with the `chown`.
  - `test_windows_not_writable_changes_nothing_and_gives_the_command`: text contains `-I -m archicad_mcp.tapir_install --archicad 29`, the sha256, and `Add-Ons\Tapir` or `Add-Ons/Tapir` (path as given).
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement.** **Step 4: Run**, PASS.
- [ ] **Step 5: Commit** `feat: stage, back up and install Tapir with rollback`.

### Task 6: Orchestration and CLI

**Files:**
- Create: `src/archicad_mcp/tapir_install/run.py`, `src/archicad_mcp/tapir_install/cli.py`, `src/archicad_mcp/tapir_install/__main__.py`
- Modify: `src/archicad_mcp/server.py` (`main()`: first argument `install-tapir` hands `sys.argv[2:]` to `tapir_install.cli.main` and exits with its code)
- Test: `tests/test_tapir_cli.py`

**Interfaces:**
- Consumes: Tasks 1 to 5.
- Produces:
  - `@dataclass class Report: release: Release; rows: list[Row]; outcomes: list[Outcome]; dry_run: bool; notes: list[str]` with `to_dict() -> dict` (JSON-safe: paths as strings, datetimes ISO) used by the MCP tool.
  - `run(*, versions: set[int] | None = None, replace: bool = False, dry_run: bool = True, latest: bool = False, from_file: Path | None = None, add_ons_folder: Path | None = None, confirm_rows: Callable[[list[Row]], list[Row]] | None = None, client: httpx.Client | None = None, installations: list[Installation] | None = None, running: dict[int, str] | None = None, platform: str | None = None, backups: Path | None = None, prefs_dir: Path | None = None) -> Report`
    - order: release (pinned, or `fetch_latest`); installations (detected, or the single `add_ons_folder` one, which needs exactly one version); one temp dir; download and check the asset for every row with an asset (or check `from_file`, which needs exactly one version and the asset's exact name); `build_plan` with `reference`; when not `dry_run`, rows with action `install`/`replace` go through `confirm_rows` (default: all), then `stage` and `apply_row`; temp dir removed in `finally`.
    - a download or check failure for one version marks that row `skip` with the error in its notes and carries on with the others.
  - `format_report(report: Report) -> str` (one block per row: version, Add-Ons folder, each entry with kind, date, size, identical yes/no/unknown, running version, state, action, notes; then outcomes with backup folder, the restore line `to restore: move <backup>/<name> back to <original folder>`, and the restart note)
  - `cli.main(argv: list[str]) -> int` with flags `--archicad N` (repeatable), `--dry-run`, `--replace`, `--yes`, `--from-file PATH`, `--latest`, `--add-ons-folder PATH`. Exit codes: `0` done or nothing to do, `1` any error, `2` writes needed a yes that was not given.
    - interactive (`sys.stdin.isatty()`), not `--dry-run`, not `--yes`: one prompt for the installs, one per replacement; `--yes` answers the prompts; non-interactive without `--yes`: report printed as a dry run plus `Pass --yes to install.`, exit 2.

- [ ] **Step 1: Write the failing tests** (everything injected: `installations`, a MockTransport `client` serving fake assets whose `Release` is built in the test, `running={}`, `backups=tmp_path/"b"`):
  - `test_run_dry_run_downloads_checks_and_writes_nothing`: Add-Ons trees unchanged (compare a recursive listing before and after), report rows have `identical` filled where entries exist.
  - `test_run_installs_missing_and_leaves_different_alone`
  - `test_run_one_failed_download_does_not_stop_the_others`
  - `test_from_file_needs_one_version_and_the_exact_name`
  - `test_cli_without_terminal_or_yes_writes_nothing`: `monkeypatch.setattr(sys.stdin, "isatty", lambda: False)`; `cli.main([...])` returns 2; tree unchanged; output contains `Pass --yes`.
  - `test_cli_prompt_no_means_nothing`: isatty True, `input` patched to return `"n"`; nothing written; exit 0.
  - `test_server_main_dispatches_install_tapir`: `sys.argv = ["archicad-mcp", "install-tapir", "--dry-run"]` with `cli.main` patched; `server.main()` raises `SystemExit` with the patched return value and never builds the server.
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement.** `cli.main` builds real objects (`make_client()`, `find_installations()`, `running_tapir_versions()`) only when nothing is injected. **Step 4: Run**, PASS.
- [ ] **Step 5: Commit** `feat: archicad-mcp install-tapir`.

### Task 7: Hint text, connection message, banner, list_instances

**Files:**
- Create: `src/archicad_mcp/tapir_install/hint.py`
- Modify: `src/archicad_mcp/connection.py`, `src/archicad_mcp/server.py` (`_instance_line`, `list_instances`), `src/archicad_mcp/extract.py` (`SELECTION_PARTIAL_NOTE` users), `src/archicad_mcp/core/definitions.py:145`
- Test: `tests/test_tapir_hint.py`, additions to `tests/test_connection.py`, `tests/test_startup_banner.py`, `tests/test_tier1_tools.py`

**Interfaces:**
- Produces:
  - `install_hint(archicad_version: int | None, platform: str | None = None, *, add_ons: Path | None = None) -> str`, e.g. for 30 on darwin: `Install it with 'archicad-mcp install-tapir --archicad 30' or the install_tapir tool, or by hand: download TapirAddOn_AC30_Mac.zip from https://github.com/ENZYME-APD/tapir-archicad-automation/releases/tag/1.7.1, unzip it into <add_ons>/Tapir/ (the Archicad 30 Add-Ons folder when not known), and restart Archicad.` With `archicad_version=None`, the asset is given as the pattern `TapirAddOn_AC<version>_Mac.zip`. On win32 the asset is the `.apx` and the step is "copy it into".
  - `ArchicadConnection.archicad_version() -> int | None` (lazy `API.GetProductInfo`, cached, `None` on any API error)
  - `ArchicadConnection.tapir()` missing message: `'<command>' requires the Tapir add-on, which is not installed in Archicad <N> on port <port>. ` + `install_hint(...)`.
  - `InstanceInfo` gains `tapir_hint: str | None = None` and `tools_needing_newer_tapir: list[str] = field(default_factory=list)`, filled in `probe_port`: hint when Tapir is missing; when present, `tools_needing_newer_tapir` from `tapir_requirements.unavailable_tools(conn)` (Task 8), and when that is not empty a hint ending `Replacing a custom Tapir build with the release loses that build's own commands.` (`probe_port` imports `tapir_requirements` lazily to avoid a cycle.)
  - Banner: the "not installed" line ends with `; install with: archicad-mcp install-tapir --archicad <N>`.

- [ ] **Step 1: Write the failing tests**:
  - `test_install_hint_mac_and_windows`: darwin text contains `TapirAddOn_AC30_Mac.zip`, `releases/tag/1.7.1`, `install-tapir --archicad 30`, `install_tapir`; win32 contains `TapirAddOn_AC29_Win.apx` and `copy it into`.
  - `test_tapir_missing_message_names_the_asset` (in `test_connection.py`): `TAPIR_OFF` core (version 29), `conn.tapir("GetStories")` raises with `TapirAddOn_AC29_` and `port 19723`.
  - `test_probe_port_fills_hint_when_tapir_missing`
  - `test_banner_names_install_command_when_tapir_missing`
  - Existing tests that build `InstanceInfo` positionally still pass (new fields are last, with defaults).
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement**, replacing the "Options > Add-On Manager" sentence in `connection.py`; in `extract.py` and `definitions.py` append `install_hint(conn.archicad_version())` where the note is built and a connection is at hand. **Step 4: Run** the new tests and `uv run pytest tests/test_connection.py tests/test_startup_banner.py tests/test_extract.py tests/test_definitions.py -q`, PASS.
- [ ] **Step 5: Commit** `feat: missing-Tapir messages name the exact asset and the install command`.

### Task 8: Per-tool Tapir requirements and gates

**Files:**
- Create: `src/archicad_mcp/tapir_requirements.py`
- Modify: `src/archicad_mcp/server.py` (gated tools), `src/archicad_mcp/core/definition_edit.py` (`editing_unavailable` uses the shared refusal)
- Test: `tests/test_tapir_requirements.py`

**Interfaces:**
- Consumes: `gateway.registry.build_registry()`, `ArchicadConnection.tapir_command_available`, `ArchicadConnection.archicad_version`, `install_hint`.
- Produces:
  - `TOOL_COMMANDS: dict[str, tuple[str, ...]]`, the commands every call needs:
    - `move_elements: ("MoveElements",)`, `delete_elements: ("DeleteElements",)`
    - `reserve_elements: ("ReserveElements",)`, `release_elements: ("ReleaseElements",)`
    - `list_issues: ("GetIssues",)`, `create_issue: ("CreateIssue",)`, `add_issue_comment: ("AddCommentToIssue",)`, `attach_elements_to_issue: ("AttachElementsToIssue",)`, `export_issues_bcf: ("ExportIssuesToBCF",)`, `import_issues_bcf: ("ImportIssuesFromBCF",)`
    - `publish: ("PublishPublisherSet",)`, `highlight_failures: ("HighlightElements",)`, `create_issues_from_failures: ("CreateIssue", "AttachElementsToIssue")`
    - `create_swept_beam` and `create_road_marking: ("CreateObjects", "GetAvailableLibraryParts", "SetGDLParametersOfElements")`
    - `edit_property_definitions`, `edit_classifications`, `import_definitions: ("UpdateClassificationItems",)` (the existing `EDIT_MARKER`)
    - `deploy_gdl_object: ("ReloadLibraries", "CreateObjects")` (check `gdl/deploy.py` for which commands every call sends; adjust and say so in the commit)
    - Verify each entry against the tool's code before adding it; a command only some arguments use stays out.
  - `EXPLICIT_VERSIONS: dict[str, tuple[str, str]] = {"UpdateClassificationItems": ("1.6.0", "not in the synced 1.5.9 definitions; added in Tapir 1.6.0")}`
  - `FORK_ONLY: frozenset[str]` (the names in `gateway/definitions/local_commands.json`)
  - `command_version(name: str) -> str | None` (registry version, unless overridden by `EXPLICIT_VERSIONS`; `None` for `FORK_ONLY`)
  - `tool_minimum(tool: str) -> tuple[str | None, str]` (the highest version and the command that sets it; version `None` when that command is fork-only)
  - `tapir_refusal(conn, tool: str) -> dict | None` (`None` when fine; `{"error": ...}` for missing Tapir, or `'<tool>' needs Tapir <min> or newer (this Archicad has <installed or 'an unknown version'>): it lacks <command>. ` + `install_hint(...)`; fork-only text: `'<tool>' needs a Tapir build with <command>, which no Tapir release has yet.`). Probes only the command from `tool_minimum`.
  - `require_tapir_command(conn, command: str, tool: str) -> dict | None` (same texts for one command; the hook for argument-dependent commands)
  - `unavailable_tools(conn) -> list[str]` (tools in `TOOL_COMMANDS` whose minimum command is absent; one probe per distinct command)
  - In `server.py`, each gated tool starts `conn = _conn(port)` and returns `tapir_refusal(conn, "<name>")` when not `None`, before any other call. `create_elements` is not touched.

- [ ] **Step 1: Write the failing tests**:
  - `test_every_gated_tool_is_registered` (full mode with a workspace, as `test_manifest.py` builds it)
  - `test_every_command_has_a_version_or_is_fork_only`
  - `test_explicit_versions_agree_with_the_registry_where_it_has_them`
  - `test_pinned_release_meets_every_release_backed_minimum` (`Version(load_pinned().version) >= Version(min)`; also `>= tapir_version.json["version"]`)
  - `test_refusal_names_both_versions`: fake conn with `IsAddOnCommandAvailable` returning `available: False` only for `UpdateClassificationItems` and `GetAddOnVersion` answering `1.5.9`; `tapir_refusal(conn, "edit_classifications")["error"]` contains `1.6.0`, `1.5.9`, `UpdateClassificationItems`, `install-tapir`.
  - `test_fork_only_refusal_says_no_release`: `require_tapir_command(conn, "CreateRailings", "create_elements")` contains `no Tapir release has yet`.
  - `test_gated_tool_sends_nothing_after_refusal`: via the server (`fake_archicad`-style fixture) `move_elements` with Tapir off returns the missing message and `core.calls` holds only the probes (`API.IsAddOnCommandAvailable`, `API.GetProductInfo`), no `MoveElements`.
  - `test_unavailable_tools_lists_editing_tools_on_old_tapir`
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement.** `IsAddOnCommandAvailable` fakes need a callable response keyed on `addOnCommandId.commandName`. **Step 4: Run** the file and the whole suite (`uv run pytest -q`), PASS.
- [ ] **Step 5: Commit** `feat: per-tool minimum Tapir versions, checked before sending`.

### Task 9: The install_tapir tool, manifest, dashboard, README

**Files:**
- Modify: `src/archicad_mcp/server.py` (register `install_tapir` in `_register_full_mode_tools`), `manifest.json`, `scripts/build_dashboard.py` (TOOLS entry, category "Setup" or the closest existing one), `docs/api-dashboard.html` (regenerated), `tests/test_tool_annotations.py` (`install_tapir` in `WRITERS`, destructive), `README.md`
- Test: `tests/test_tapir_tool.py`

**Interfaces:**
- Consumes: `run`, `Report.to_dict()`.
- Produces: `install_tapir(archicad_version: int | None = None, replace_existing: bool = False, confirm: bool = False) -> dict` returning `Report.to_dict()` plus `restart_required: list[int]` and `backups: list[str]`; `dry_run = not confirm`. Description: `Install the pinned Tapir add-on (1.7.1) into Archicad's Add-Ons folder. Dry run by default: it downloads and checks the file and says what it would do. confirm=true writes. Never replaces an existing Tapir unless replace_existing=true, and then moves the old copy to a backup folder. On macOS a protected Add-Ons folder brings up the system password dialog. Archicad must be restarted afterwards. Needs no running Archicad.` Annotations: `_tool_meta("Install the Tapir add-on", read_only=False, destructive=True)`.

- [ ] **Step 1: Write the failing tests**: `test_install_tapir_registered_only_in_full_mode`; `test_install_tapir_without_confirm_is_a_dry_run` (patch `tapir_install.run.run` to capture kwargs: `dry_run is True`, `replace is False`); `test_install_tapir_confirm_passes_through` (`dry_run is False`, `versions == {30}`, `replace is True`).
- [ ] **Step 2: Run**, FAIL. **Step 3: Implement** the tool, the manifest entry (`"Install the pinned Tapir add-on into Archicad's Add-Ons folder. Dry run by default."`), the dashboard entry, regenerate with `uv run python scripts/build_dashboard.py`.
- [ ] **Step 4: README**: Requirements bullet (Tapir installed for you, pinned 1.7.1, exercised live version stated honestly); a new section `## Install Tapir` after the install sections: the command, `--dry-run`, the flags, existing copies and backups, the macOS password dialog, the Windows administrator terminal, the `.mcpb` route (ask for `install_tapir`), the manual route (`TapirAddOn_AC<version>_<Mac.zip|Win.apx>` into `Add-Ons/Tapir/`, restart); "Check it works" mentions `tapir_hint`.
- [ ] **Step 5: Run** `uv run pytest -q`, PASS; `grep -rn $'—\|–'` over changed files finds nothing.
- [ ] **Step 6: Commit** `feat: install_tapir tool; README Install Tapir section`.

### Task 10: Acceptance dry runs on this Mac

- [ ] `uv run archicad-mcp install-tapir --dry-run`: expect rows for Archicad 26, 27, 28, 29, 30; 27 and 29 `different Tapir present` (loose), skipped; 26, 28, 30 `install`; nothing written (`ls` of the five Add-Ons folders before and after match).
- [ ] `uv run archicad-mcp install-tapir --archicad 30 --dry-run`: `TapirAddOn_AC30_Mac.zip` downloaded, sha256 `a08e6304...` matches, would install into `/Applications/Graphisoft/Archicad 30/Add-Ons/Tapir/`.
- [ ] Save both outputs to the shared notes folder and stop. The real install waits for Aleš.
