# save_project, show_elements, export_ifc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three curated tools (save the project, show elements in Archicad, export IFC) and a zoom in `highlight_failures`.

**Architecture:** One core module per tool (`core/save.py`, `core/views.py`, `core/ifc_export.py`), registered in `server.py` with `_tool_meta` and `_guarded` like the existing tools. Zoom and highlight-clearing helpers join `actions.py`, which `highlight_failures` already uses. Minimum Tapir per tool in `tapir_requirements.TOOL_COMMANDS`.

**Tech Stack:** Python 3.12+, FastMCP, multiconn_archicad, pytest with `tests.conftest.FakeCore`.

**Spec:** `docs/superpowers/specs/2026-10-19-save-show-export-design.md`

## Global Constraints

- No em or en dashes anywhere: code, comments, docs, commit messages.
- Floor plan database id: `D5D16DD4-093F-4674-895D-410D634D8C7E`.
- Minimum Tapir: `save_project` `SaveProject` (1.3.1); `show_elements` `ChangeWindow`, `FitInWindow`, `ChangeSelectionOfElements`, `HighlightElements`, `GetNavigatorItemTree`, `GetDatabaseIdFromNavigatorItemId` (1.3.1); `export_ifc` `IFCFileOperation`, `GetIFCExportTranslators` (1.6.0). `highlight_failures` stays at `HighlightElements`.
- Hints: `save_project` and `export_ifc` destructive writers; `show_elements` a non-destructive writer.
- `save_project` registers in both modes and omits project name and location in verdicts mode; `show_elements` and `export_ifc` in full mode only.
- Scopes, exactly: `EntireProject`, `VisibleElementsOnAllStories` (default), `AllElementsOnCurrentStory`, `VisibleElementsOnCurrentStory`, `SelectedElementsOnly`.
- Database walk cap: 300 databases.
- Every new tool needs its row in `manifest.json`, `scripts/build_dashboard.py` `TOOLS` (and `WRAPPED_BY` for its commands), and `tests/test_tool_annotations.py` `WRITERS` (plus `NON_DESTRUCTIVE_WRITERS` for `show_elements`), in the same task, or the existing suite fails.
- Run the suite with `uv run pytest -q` (excluding nothing; `tests/test_live.py` skips without Archicad).

## Review Focus

- GUIDs given in lower case or repeated: matched case-insensitively against Tapir's upper-case GUIDs, deduplicated, reported in the caller's spelling. Test in Task 2.
- A story with an empty name (MCP-Test's stories 1 and 2 are `""` in `GetStories`): `story_name` falls back to `"Story <index>"`. Test in Task 2.
- A Teamwork project location (`teamwork://...`) in `save_project`: no modification-time or `os.access` check, no token in the result. Test in Task 1.
- `export_ifc` path given relative, with `~`, with an upper-case `.IFC`, or in a missing folder: relative and missing folder refused, `~` expanded, `.IFC` accepted as `ifc`. Test in Task 4.
- Translator name given with different case: matched; a name matching nothing lists every name. Test in Task 4.

---

### Task 1: save_project

**Files:**
- Create: `src/archicad_mcp/core/save.py`
- Modify: `src/archicad_mcp/server.py` (tier 1, beside `highlight_failures`), `src/archicad_mcp/tapir_requirements.py`, `manifest.json` (tool row; "8 QA tools" becomes 9 in the mode description), `scripts/build_dashboard.py`, `tests/test_tool_annotations.py`
- Test: `tests/test_save_project.py`

**Interfaces:**
- Produces: `save_project(conn: ArchicadConnection, confirm: bool, show_identity: bool) -> dict` in `core/save.py`.
- Consumes: `core.project._scrub` for the location.

- [ ] **Step 1: Write the failing tests** in `tests/test_save_project.py`, calling the tool through `build_server` and `Client` like `tests/test_tier2_project_issues.py`, with `GetProjectInfo` and `SaveProject` canned on a `FakeCore`, and a real temporary file as the local project location:
  - `test_dry_run_sends_nothing`: `confirm` omitted; result has `dry_run: True`, `project`, `location`, `is_teamwork: False`; `SaveProject` not in `core.calls`.
  - `test_untitled_is_refused_before_sending`: `isUntitled: True`; `"File > Save As"` in `error`; no `SaveProject` call.
  - `test_save_reports_file_updated`: `SaveProject` callable that bumps the temp file's mtime (`os.utime`); `saved: True`, `file_updated: True`.
  - `test_save_that_left_the_file_reports_it`: `SaveProject` returns `{"success": True}` without touching the file; `file_updated: False` and a `note`.
  - `test_failure_names_the_older_version_case`: `SaveProject` raises `TapirCommandError(message="Failed to save the project.", code=-2130313112)`; `error` contains the Archicad message, the code, `"older Archicad"` and `"Save As"`.
  - `test_failure_says_when_the_folder_is_not_writable`: monkeypatch `os.access` to return False; the `error` contains `"not writable"`.
  - `test_teamwork_saves_locally_and_sends_nothing`: `isTeamwork: True`, location `teamwork://u:eyJhbGciOiJIUzI1NiJ9.cmVmcmVzaA.s1g@host/P`; commit gives `sent: False`, a note containing `"Send"`, no `file_updated`, and `"eyJ"` nowhere in the JSON.
  - `test_verdicts_mode_hides_identity`: `build_server(mode="verdicts")`; dry run result has no `project` and no `location`.
  - `test_needs_save_project_command`: `API.IsAddOnCommandAvailable` false for `SaveProject`; `error` names Tapir 1.3.1.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_save_project.py -q`
Expected: FAIL, unknown tool `save_project`.

- [ ] **Step 3: Implement `save_project` in `core/save.py`** and register it in `build_server` tier 1 as `save_project(confirm: bool = False, port: int | None = None)`, `_tool_meta("Save the project", read_only=False, destructive=True)`, calling `require_tapir(conn, "save_project")`, `show_identity=(mode == "full")`. The description says: confirm-gated; saves the open project to its own file (no Save As); on Teamwork saves the local copy and never sends; call it before the property reads that can crash Archicad 29 (`get_element_data`, `set_element_data`, `find_elements` with property comparisons, `audit_delivery_readiness`, `run_rule`, `verify_ifc_export_readiness`, `get_model_summary` with `include_layer_story`). File time check only when the location is an existing local path (`Path(location).is_file()`); catch `APIErrorBase` from `SaveProject` and build the error text from it. Add `"save_project": ("SaveProject",)` to `TOOL_COMMANDS`; manifest, dashboard (`"cat": "Discovery & model"`, `"mode": "both"`, `"mutates": True`; `WRAPPED_BY["SaveProject"]`), and `WRITERS`.

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/save.py src/archicad_mcp/server.py src/archicad_mcp/tapir_requirements.py manifest.json scripts/build_dashboard.py tests/test_save_project.py tests/test_tool_annotations.py
git commit -m "feat: save_project saves the open project, confirm-gated"
```

### Task 2: show_elements, locating and choosing the view

**Files:**
- Create: `src/archicad_mcp/core/views.py`
- Test: `tests/test_show_elements.py`

**Interfaces:**
- Produces, in `core/views.py`:
  - `FLOOR_PLAN_DB = "D5D16DD4-093F-4674-895D-410D634D8C7E"`, `WALK_CAP = 300`
  - `@dataclass View: kind: str` (`"FloorPlan"` or the navigator item type), `name: str`, `navigator_item_id: str | None`, `database_id: str`
  - `@dataclass Located: floor_plan: dict[str, int]` (guid -> story index), `elsewhere: dict[str, View]`, `not_found: list[str]`, `not_searched: list[str]`
  - `locate(conn, guids: list[str], window: str | None) -> Located`
  - `choose(located: Located, window: str | None, active_story: int) -> tuple[View | None, int | None]` (the view and, for the floor plan, the story; `(None, None)` for 3D, meaning "stay in 3D")
- Consumes: `extract.element_payload`, `editability._details` (with its `fields` fallback).

- [ ] **Step 1: Write the failing tests** with a `FakeCore` whose `GetAllElements` answers per `databases[0].databaseId.guid`, `GetNavigatorItemTree` per `navigatorMapId`, `GetDatabaseIdFromNavigatorItemId` in request order, `GetDetailsOfElements` with `floorIndex` or an `error` entry:
  - `test_floor_plan_elements_get_their_story`
  - `test_lower_case_and_repeated_guids_match_once` (input `["abc", "ABC"]`, Tapir knows `"ABC"`; one entry, keyed `"abc"`)
  - `test_section_element_is_found_in_its_database` (the walk asks only Project Map and Layout Book items of the listed view types; `View.name == "Building Section"`)
  - `test_walk_stops_once_everything_is_placed` (two section databases, element in the first: one `GetAllElements` with `databases` beyond the floor plan one)
  - `test_unreadable_guid_skips_the_walk_from_the_floor_plan` (`window="FloorPlan"`, details answer an error: `not_found`, no navigator calls)
  - `test_from_a_layout_the_walk_runs_even_for_unreadable_guids` (`window="Layout"`: navigator calls happen)
  - `test_walk_cap_leaves_the_rest_not_searched` (monkeypatch `WALK_CAP = 1`, two databases, element in the second: `not_searched == [guid]`)
  - `test_choose_picks_the_story_holding_most` / `test_choose_tie_goes_to_the_active_story` / `test_choose_floor_plan_wins_a_tie_with_a_view` / `test_choose_stays_in_3d_for_model_elements`
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_show_elements.py -q`
Expected: FAIL, `ModuleNotFoundError: archicad_mcp.core.views`.

- [ ] **Step 3: Implement `locate` and `choose`.** Project Map item types walked: `SectionItem`, `ElevationItem`, `InteriorElevationItem`, `DetailDrawingItem`, `WorksheetDrawingItem`, `DocumentFrom3DItem`; Layout Book: `LayoutItem`, `MasterLayoutItem`. Order: Project Map views, then layouts, then masters; when every remaining GUID read as a `Drawing`, layouts first. Several items with one database keep the first. A walk listing failure (`APIErrorBase`) puts the remaining GUIDs in `not_searched`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_show_elements.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/views.py tests/test_show_elements.py
git commit -m "feat: locate elements across the floor plan and every view database"
```

### Task 3: show_elements, the tool, and the zoom in highlight_failures

**Files:**
- Modify: `src/archicad_mcp/core/views.py`, `src/archicad_mcp/actions.py`, `src/archicad_mcp/server.py`, `src/archicad_mcp/tapir_requirements.py`, `manifest.json`, `scripts/build_dashboard.py`, `tests/test_tool_annotations.py`
- Test: `tests/test_show_elements.py`, `tests/test_tier1_tools.py` (highlight_failures)

**Interfaces:**
- Consumes: Task 2's `locate`, `choose`, `View`, `Located`; `editability.active_window`; `selection.set_selection` logic (deselect current, select new, one call).
- Produces:
  - `actions.fit_in_window(conn, guids: list[str]) -> bool` (False when `FitInWindow` is not available or Archicad refuses)
  - `actions.clear_highlight(conn) -> None` (`HighlightElements` with empty `elements` and `highlightedColors`)
  - `views.show_elements(conn, guids: list[str], select: bool, highlight: bool, zoom: bool) -> dict` with the result keys of the spec.

- [ ] **Step 1: Write the failing tests** (through the server tool, `FakeCore` recording calls):
  - `test_switches_story_and_confirms_it` (active story 0, elements on 1: `ChangeWindow {"windowType": "FloorPlan", "storyIndex": 1}`, `GetStories` then reports 1; `view == {"type": "FloorPlan", "story_index": 1, "story_name": "First"}`, `switched` names both)
  - `test_story_that_did_not_move_is_a_warning` (`GetStories` keeps 0: a warning naming story 1)
  - `test_old_tapir_rejecting_story_index_retries_without_it` (`ChangeWindow` raises for `storyIndex`; second call without it; warning)
  - `test_empty_story_name_falls_back` (`"Story 2"`)
  - `test_section_view_is_opened_by_navigator_item` (`ChangeWindow {"navigatorItemId": {"guid": ...}}`, `GetCurrentWindowType` read back)
  - `test_selection_is_read_back` (`GetSelectedElements` after selecting returns one of two: `selected == 1`, `not_selectable == [other]`)
  - `test_highlight_false_clears_the_highlight` (`HighlightElements` with `elements == []`)
  - `test_empty_guids_clears_highlight_and_selection` (no `ChangeWindow`, no `FitInWindow`)
  - `test_other_stories_and_views_are_listed_not_shown`
  - `test_no_window_change_when_already_there`
  - `test_highlight_failures_zooms` and `test_highlight_failures_without_fit_in_window_says_so` (in `tests/test_tier1_tools.py`)
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_show_elements.py tests/test_tier1_tools.py -q`
Expected: FAIL, unknown tool `show_elements` and no `zoomed` key.

- [ ] **Step 3: Implement.** `show_elements` registers in `_register_full_mode_tools` as `show_elements(guids: list[str], select: bool = True, highlight: bool = True, zoom: bool = True, port: int | None = None)`, `_tool_meta("Show elements in Archicad", read_only=False, destructive=False)`, through `_tapir_conn(port, "show_elements")`. On the floor plan, select and highlight every floor-plan element found, zoom to the chosen story's. In another view, the view's elements only. `highlight_failures` gains `zoom: bool = True` and returns `zoomed`, with a note pointing to `show_elements` when the failures span stories (or when zoom is unavailable); its description says `show_elements` with `guids=[]` clears the highlight. Add the `TOOL_COMMANDS` entry, manifest, dashboard (`"cat": "Elements"`, `WRAPPED_BY` for `ChangeWindow`, `FitInWindow`, `GetNavigatorItemTree`, `GetDatabaseIdFromNavigatorItemId`, and `show_elements` appended to `ChangeSelectionOfElements`, `HighlightElements`, `GetStories`, `GetCurrentWindowType`, `GetAllElements`, `GetDetailsOfElements` where those keys exist), `WRITERS` and `NON_DESTRUCTIVE_WRITERS`.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests manifest.json scripts/build_dashboard.py
git commit -m "feat: show_elements selects, highlights, opens the right story or view and zooms; highlight_failures zooms"
```

### Task 4: export_ifc

**Files:**
- Create: `src/archicad_mcp/core/ifc_export.py`
- Modify: `src/archicad_mcp/server.py`, `src/archicad_mcp/tapir_requirements.py`, `manifest.json`, `scripts/build_dashboard.py`, `tests/test_tool_annotations.py`
- Test: `tests/test_export_ifc.py`

**Interfaces:**
- Produces: `export_ifc(conn, path: str, translator: str | None, scope: str, overwrite: bool, dry_run: bool) -> dict`; `SCOPES: tuple[str, ...]` (the five names in the Tapir enum order).
- Consumes: `editability.active_window`, `extract.get_selected_element_ids`.

- [ ] **Step 1: Write the failing tests** (temp folder from `tmp_path`; `GetIFCExportTranslators` canned with `[{"name": "General Parametric Export", "preview": True}, {"name": "IFC4 Reference View-based Export", "preview": False}]`; `IFCFileOperation` callable that writes a few bytes to `ifcFilePath`):
  - `test_dry_run_shows_translator_path_and_scope` (default translator, `translator_is_default: True`, `file_type: "ifc"`, `translators` both names, no `IFCFileOperation` call)
  - `test_translator_matches_ignoring_case`
  - `test_unknown_translator_lists_the_names`
  - `test_scope_is_validated_and_matched_ignoring_case`
  - `test_relative_path_is_refused` / `test_missing_folder_is_refused` / `test_tilde_is_expanded` (monkeypatch `HOME` to `tmp_path`) / `test_upper_case_extension_is_ifc` / `test_ifczip_extension` / `test_other_extension_is_refused`
  - `test_existing_file_is_refused_without_overwrite` (dry run and commit) and `test_overwrite_replaces_it`
  - `test_commit_sends_the_save_and_reports_the_file` (params exactly `{"method": "save", "ifcFilePath": <path>, "fileType": "ifc", "translatorName": "General Parametric Export", "elementsToExport": "VisibleElementsOnAllStories"}`; `exported`, `bytes > 0`, `seconds`)
  - `test_success_without_a_file_is_an_error`
  - `test_section_window_switches_to_the_floor_plan` (dry run `note` says so; commit sends `ChangeWindow {"windowType": "FloorPlan"}` before the save and reports `switched_window`)
  - `test_selected_only_needs_a_selection` and `test_selected_only_refused_from_another_window`
  - `test_current_story_scope_names_the_story`
  - `test_needs_tapir_1_6_0` (`GetIFCExportTranslators` unavailable: error names 1.6.0)
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_export_ifc.py -q`
Expected: FAIL, unknown tool `export_ifc`.

- [ ] **Step 3: Implement** and register `export_ifc(path: str, translator: str | None = None, scope: str = "VisibleElementsOnAllStories", overwrite: bool = False, dry_run: bool = True, port: int | None = None)` in full mode, `_tool_meta("Export IFC", read_only=False, destructive=True)`, via `_tapir_conn(port, "export_ifc")`. Description: the step after `verify_ifc_export_readiness`; dry run by default; translator by name (call with a wrong name or dry run to list them); scopes; refuses to overwrite; may switch to the floor plan. `TOOL_COMMANDS["export_ifc"] = ("IFCFileOperation", "GetIFCExportTranslators")`; manifest; dashboard (`"cat": "Issues & publishing"`, `WRAPPED_BY` for both commands); `WRITERS`.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/ifc_export.py src/archicad_mcp/server.py src/archicad_mcp/tapir_requirements.py manifest.json scripts/build_dashboard.py tests/test_export_ifc.py tests/test_tool_annotations.py
git commit -m "feat: export_ifc saves IFC with a named translator, dry run first"
```

### Task 5: Documentation and dashboard

**Files:**
- Modify: `README.md` (warning box points to `save_project`; QA list gains `save_project`; core list gains `show_elements`, `export_ifc`; one paragraph each; "The 8 QA tools" becomes 9; Teamwork save unverified live), `docs/known-issues.md` ("Save first" names `save_project`), `docs/api-dashboard.html` (regenerated)

- [ ] **Step 1: Edit the docs**, then regenerate: `uv run python scripts/build_dashboard.py`
- [ ] **Step 2: Check for dashes**: `grep -n "—\|–" README.md docs/known-issues.md docs/superpowers/plans/2026-10-19-save-show-export.md src/archicad_mcp/core/save.py src/archicad_mcp/core/views.py src/archicad_mcp/core/ifc_export.py` prints nothing.
- [ ] **Step 3: Run the whole suite**: `uv run pytest -q`, all pass.
- [ ] **Step 4: Commit**: `git commit -am "docs: save_project, show_elements and export_ifc"`

### Task 6: Live check

Only after `list_instances` shows `MCP-Test` (Archicad 29, port 19723) or `MCP-Test_30` (port 19724). A script in the scratchpad calls the core functions on a real `ArchicadConnection`; no test elements are created.

- [ ] `save_project` dry run, then commit on MCP-Test: `saved`, and whether `file_updated` is true with nothing unsaved (record it).
- [ ] `show_elements`: floor plan elements on two stories (switches to the story with more, lists the other), a section element (opens the section), a layout drawing (opens the layout), `guids=[]` (clears). Floor plan and story restored at the end; the selection left empty as found.
- [ ] `highlight_failures` with a bundled rule: `zoomed: true`; then clear.
- [ ] `export_ifc` dry run and commit to the scratchpad folder with "General Parametric Export"; a second commit without `overwrite` refused; the files deleted.
- [ ] Record the findings in the spec's facts section and commit.
