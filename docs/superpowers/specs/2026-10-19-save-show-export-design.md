# save_project, show_elements, export_ifc: Design

**Date:** 2026-10-19
**Status:** Written from the brief of the parent session (items 3, 4 and 5 of the ranked list in
`2026-10-19-tapir-command-triage.md`, main checkout, not committed). Next: the implementation plan.

## Purpose

Three small curated tools over Tapir commands that the gateway already reaches, each with the checks
and the honest result an agent needs:

1. **`save_project`**: save the open project, so an agent can protect unsaved work before the property
   reads that can crash Archicad 29.
2. **`show_elements`**: select, highlight, switch to the right story or view, and zoom, so an agent can
   show the user what it means instead of describing GUIDs. `highlight_failures` also zooms.
3. **`export_ifc`**: save the project as IFC with a translator chosen by name, the step after
   `verify_ifc_export_readiness`.

Done means: this spec, the implementation with unit tests, a live check on MCP-Test (Archicad 29) or
MCP-Test_30 (Archicad 30), and one PR with CI green.

## Decisions from the brief

- `save_project` over Tapir `SaveProject`. Report plainly the case where Archicad will only Save As (an
  Archicad 27 file opened in Archicad 28 answered "Failed to save the project"). On Teamwork, saving is
  not sending.
- A `save_first` flag on the reading tools was to be considered (decided below: not added).
- `show_elements` uses `ChangeWindow` and `FitInWindow`; elements of another view's database exist
  (layouts, sections).
- `export_ifc` uses `IFCFileOperation` save with a translator chosen by name from
  `GetIFCExportTranslators` (Tapir 1.6.0+). Open and merge stay out. The dry run shows translator, path
  and scope. An existing file is never overwritten unless asked.
- Existing patterns: `_tool_meta` hints, confirm or dry run for writes, `TOOL_COMMANDS` for the
  minimum Tapir.

## Facts the design rests on

Read from the Tapir 1.7.1 source (`git show 1.7.1:archicad-addon/Sources/...`) and probed live on
MCP-Test_30 (Archicad 30, Tapir 1.7.1) on 19.10.2026, view state only, restored afterwards.

- **SaveProject** (1.3.1, `ProjectCommands.cpp`): switches to the floor plan around a plain
  `ACAPI_ProjectOperation_Save` and restores the window (a save from a 3D or section window failed with
  APIERR_READONLY, #681). Any failure answers "Failed to save the project." with Archicad's code, which
  multiconn raises as `TapirCommandError`. The plain save keeps every refusal File > Save makes (a
  read-only project, a stale lock). `GetProjectInfo` has `isUntitled`, `isTeamwork` and the location;
  nothing reports unsaved changes or the file's Archicad version.
- **ChangeWindow** (1.3.1, `ApplicationCommands.cpp`): takes a `navigatorItemId` (Archicad 27+, goes to
  the view with its saved settings) or a `windowType` with an optional `databaseId`, or `FloorPlan`
  with a `storyIndex`. `storyIndex` exists since 1.5.4, but only from 1.7.0 does it really change the
  story (#748: before, it answered success and kept the old story).
- **FitInWindow** (1.3.1, `NavigatorCommands.cpp`): `ACAPI_View_ZoomToElements` on the given elements,
  or a fit-all without them. Live it answered success for an element on another story and for section
  elements zoomed from the floor plan, so success says nothing about what is on screen.
- **Selection**: `ChangeSelectionOfElements` answers success per element and still selects nothing
  for some of them (live: railing sub-elements, one floor-plan Object, section Objects even with the
  section window in front). Only a `GetSelectedElements` read back tells what was selected. Elements on
  another story than the active one were selected.
- **HighlightElements** sets one global highlight map; an empty list clears it. Nothing reads it back.
- **Which database holds an element**: `GetAllElements` takes `databases` (since 1.1.4 or earlier) and
  switches only the API's current database, never the window, restoring it afterwards. The floor plan
  database has the fixed id `D5D16DD4-093F-4674-895D-410D634D8C7E`. `GetNavigatorItemTree`
  (ProjectMap, LayoutBook) plus `GetDatabaseIdFromNavigatorItemId` map each view to its database. Live
  on MCP-Test_30: one `GetAllElements` per database took 0.03 to 0.3 s; `ChangeWindow` with a Section
  or Layout `navigatorItemId` brought that window to the front and made it the current database.
- **IFCFileOperation save with `translatorName`** (1.6.0, `ProjectCommands.cpp`): Automate API save,
  file type `ifc` or `ifczip` (Archicad 27+), `elementsToExport` one of `EntireProject`,
  `VisibleElementsOnAllStories` (default), `AllElementsOnCurrentStory`, `VisibleElementsOnCurrentStory`,
  `SelectedElementsOnly`. An unknown translator name is refused with the list of names. The floor plan
  or the 3D window has to be in front. Without `translatorName` Tapir saves with whatever translator
  Archicad remembers from its own dialog, which a caller can neither see nor set, so the tool always
  names one.
- **GetIFCExportTranslators** (1.6.0): the project's translators, the preview translator first. MCP-Test
  has 16, "General Parametric Export" is the preview one.

## save_project

`save_project(confirm=False, port=None)`, destructive hint (it writes the .pln). Registered in both
modes, because `audit_delivery_readiness` and `run_rule`, the verdict tools, are the property reads
that crash; in verdicts mode the result leaves out the project name and location.

- Untitled project: refused before sending, "never saved; give it a name and a folder with File > Save
  As in Archicad". The tool does not choose where projects live.
- Without `confirm`: what it would do, sends nothing: project name, location (scrubbed with
  `project.py`'s `_scrub`, as `get_project_info` does), `is_teamwork`, and for Teamwork the note that
  this saves the local copy and sends nothing.
- With `confirm`: `SaveProject`. On success: `saved: true`, plus, for a project in a local file,
  `file_updated` from the file's modification time before and after (a save Archicad answered success
  for that left the file as it was is reported, not hidden). Teamwork: `sent: false` and "teammates see
  your changes only after Teamwork > Send"; the tool never sends. That a Teamwork save keeps the
  changes local is Graphisoft's documented behaviour of File > Save, not tested live here (the only
  Teamwork project at hand is a live one), so the README says so. `sent: false` is true either way:
  the tool calls nothing that sends.
- Failure: `{"error": ...}` that quotes Archicad's message and code and names the causes in order:
  the file was made in an older Archicad (it needs File > Save As once, which this tool does not do; seen
  with an Archicad 27 file in Archicad 28), the project was opened read-only (another user or a stale
  lock), the folder is not writable (checked with `os.access` for a local file and said when it is the
  case).
- Minimum Tapir: `SaveProject` (1.3.1).

**Not added: `save_first` on the reading tools.** My call. The reading tools carry `readOnlyHint`,
which lets a client run them without asking, and a save overwrites the .pln and removes File > Revert.
A flag that turns a read-only tool into one that writes the project file breaks the hint contract
`test_tool_annotations.py` exists to protect. Instead the `save_project` description names the tools
whose property reads can crash Archicad 29, and the README warning points to `save_project`.

## show_elements

`show_elements(guids, select=True, highlight=True, zoom=True, port=None)`, a non-destructive write
(view state only, like `set_selection`). Full mode only: the result names views and stories.

1. **Locate.** Dedupe the GUIDs (case-insensitive). One `GetAllElements` on the floor plan database
   finds the model elements and their labels, dimensions and markers; `GetDetailsOfElements` (`fields`
   `floorIndex`, falling back without `fields` like `editability._details`) gives their story. Only
   when some GUIDs are left: walk the Project Map (Section, Elevation, Interior Elevation, Detail,
   Worksheet, 3D Document items) and the Layout Book (Layout, Master Layout items), get their database
   ids in one call, and `GetAllElements` one database at a time until every GUID is placed. The rest are
   `not_found`. Bounding the walk (my call): with the floor plan or 3D in front, a
   `GetDetailsOfElements` read first drops the GUIDs it answers an error for straight into
   `not_found`, since from there that read reaches every database (live 19.10.2026: layout and section
   elements read fine), so a stale GUID costs no walk. From any other window the read is not trusted
   for that (from a Layout it missed floor plan labels, 28.09.2026) and the walk covers everything. The
   walk stops at 300 databases; GUIDs still unplaced then go to `not_searched`, with a warning, never
   to `not_found`. Drawings are looked for in layouts first.
2. **Choose one view.** The view holding the most of the elements wins; a tie goes to the floor plan,
   then to the first view in navigator order. On the floor plan, the story holding the most of them
   wins, a tie going to the active story, then the lowest index. Exception: with the 3D window in front
   and every element a floor plan element, stay in 3D and show them all.
3. **Switch.** Floor plan: `ChangeWindow {windowType: FloorPlan, storyIndex}` when the window or the
   story differs, then `GetStories` to confirm the story moved; when it did not (Tapir before 1.7.0),
   a warning names the story to open by hand. Another view: `ChangeWindow {navigatorItemId}`, then
   `GetCurrentWindowType` to confirm.
4. **Select** (`select=True`): replace the selection with the shown view's elements (on the floor
   plan, all of its elements, every story), the same deselect-then-select call as `set_selection`, then
   read the selection back: `selected` counts what Archicad really selected, `not_selectable` lists the
   rest.
5. **Highlight**: `highlight=True` uses `actions.highlight_elements` (same colours as
   `highlight_failures`) on the shown elements. `highlight=False` clears any earlier highlight, so
   what is on screen matches the call.
6. **Zoom** (`zoom=True`): `FitInWindow` on the elements of the chosen story or view.

Result: `shown` (count), `view` (`{"type": "FloorPlan", "story_index", "story_name"}` or
`{"type", "name", "navigator_item_id"}`), `switched` (from what to what, or false), `selected`,
`not_selectable`, `highlighted`, `zoomed`, `on_other_stories` (`[{story_index, story_name, guids}]`),
`in_other_views` (`[{type, name, guids}]`), `not_found`, `not_searched`, `warnings`. Elements on other
stories or in other views are listed, not shown; the agent can call again with them.

**Clearing** (my call): `guids=[]` removes the highlight and, with `select=True`, the selection, and
touches nothing else. The highlight also tints every other element and turns the 3D window to
wireframe, and nothing in Archicad's interface removes it, so an agent needs a way to put the view
back; the tool's description says so. `highlight_failures` had the same gap and its description now
points here. No separate tool.

Minimum Tapir: `ChangeWindow`, `FitInWindow`, `ChangeSelectionOfElements`, `HighlightElements`,
`GetNavigatorItemTree`, `GetDatabaseIdFromNavigatorItemId` (1.3.1 from the first two).

**highlight_failures** gains `zoom: bool = True`: after highlighting, `FitInWindow` on the failing
elements when this Tapir has it, and `zoomed` in the result. Its minimum Tapir stays at
`HighlightElements` (a missing `FitInWindow` means `zoomed: false` and a note, not a refusal). It does
not switch stories: failures usually span them, and the note points to `show_elements` for that.

## export_ifc

`export_ifc(path, translator=None, scope="VisibleElementsOnAllStories", overwrite=False,
dry_run=True, port=None)`, destructive hint (writes a file). Full mode only.

- **path**: absolute (a leading `~` is expanded), ending in `.ifc` or `.ifczip` (case-insensitive),
  which sets `fileType`; its folder must exist; a directory is refused. An existing file is refused,
  in the dry run as well, unless `overwrite=true`.
- **translator**: a name from `GetIFCExportTranslators`, matched exactly, then ignoring case. Unknown:
  refused with the list of names. None: the preview translator, and the result says it was the default.
- **scope**: one of the five `elementsToExport` names, matched ignoring case. For the two
  current-story scopes the dry run names the active story; for `SelectedElementsOnly` it counts the
  selection (`get_selected_element_ids`) and refuses an empty one.
- **Window**: from any window but the floor plan or 3D, the export switches to the floor plan first
  (`ChangeWindow {windowType: FloorPlan}`, keeping the story) and does not switch back; the dry run says
  so, the commit reports `switched_window`. `SelectedElementsOnly` from such a window is refused (my
  call): the selection belongs to the window in front, so the dry run would count one selection and the
  export would take another. The message says to bring the floor plan or 3D window to the front with
  the selection made there.
- **Dry run** returns `dry_run`, `path`, `file_type`, `translator`, `translator_is_default`,
  `translators` (all names), `scope`, `story` or `selected` where they apply, `active_window`,
  `exists`, and `note`.
- **Commit**: `IFCFileOperation {method: save, ifcFilePath, fileType, translatorName,
  elementsToExport}`, waiting as long as Archicad takes. Then the file must exist (and, when
  overwriting, have a newer modification time): `exported` (path), `bytes`, `seconds`, plus the
  translator, scope and file type. Success without a file is an error saying so.
- Minimum Tapir: `IFCFileOperation`, `GetIFCExportTranslators` (1.6.0, which also brought
  `translatorName`).

## Wiring

- `tapir_requirements.TOOL_COMMANDS` entries for the three tools; `test_tapir_requirements` covers the
  computed minimums.
- `manifest.json` tools, `scripts/build_dashboard.py` (`TOOLS` rows and `WRAPPED_BY` for SaveProject,
  ChangeWindow, FitInWindow, IFCFileOperation, GetIFCExportTranslators, GetNavigatorItemTree,
  GetDatabaseIdFromNavigatorItemId), `docs/api-dashboard.html` regenerated, `WRITERS` and
  `NON_DESTRUCTIVE_WRITERS` in `test_tool_annotations.py`.
- README: tool lists, one paragraph per tool, the save warning points to `save_project`.
  `docs/known-issues.md`: "Save first" names the tool.

## Testing

- Unit tests with `FakeCore` for each branch above: untitled, Teamwork, failure text, file_updated;
  locate across floor plan and other databases, view and story choice, story readback warning,
  selection readback, highlight clear; path, extension, overwrite, translator match, scope, window
  switch, missing file after success.
- Live, after `list_instances` shows MCP-Test or MCP-Test_30: `save_project` dry run and commit on the
  test model; `show_elements` on floor plan elements across two stories, on a section element and on a
  layout drawing, then the floor plan restored; `export_ifc` dry run and commit to a scratch folder
  (never his project folders), a refused overwrite, then the files removed. No test elements are
  created. MCP-Test_28 is read only (its Save As case is reproduced in unit tests, not live).

## Left to the builder

Result field order, helper names, how the navigator walk is shared, the wording of notes and errors.
Where Tapir before 1.5.4 rejects `storyIndex`, `ChangeWindow` is retried without it and the story
warning applies. `save_project` joining verdicts mode makes "the 8 QA tools" 9 in README.md and
manifest.json.

## Calls made here, for review

- No `save_first` flag (it would break the read-only hint).
- `save_project` in both modes, without project identity in verdicts mode.
- `show_elements([])` clears the highlight (and the selection with `select=True`); no separate tool.
- The database walk is skipped for GUIDs a floor-plan read cannot find, and capped at 300 databases.
- `export_ifc` defaults to the preview translator, switches a non-plan window to the floor plan without
  switching back, and refuses `SelectedElementsOnly` from other windows.
- The Teamwork save behaviour is documented as unverified live.
