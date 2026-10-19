# find_collisions and apply_favorites: Design

**Date:** 2026-10-19
**Status:** Items 8 and 9 of the ranked list in `2026-10-19-tapir-command-triage.md`. Aleš skipped the
design questions, so every decision below that is not a fact is my call and is marked as one. Next: the
implementation plan.

## Purpose

Two curated tools over Tapir 1.7.1 commands the gateway already reaches:

1. **`find_collisions`**: which elements of one group clash with which elements of another (or within
   one group), as pairs an agent can act on, plus a `no-collisions` QA rule type so clashes join
   `audit_delivery_readiness`, `highlight_failures` and `create_issues_from_failures`.
2. **`apply_favorites`**: make existing elements match an office favourite in one step ("make these 40
   doors match favourite X"), dry run first, with an honest report of what the favourite changed.

Done means: this spec, the implementation with unit tests, a live check on MCP-Test (Archicad 29), the
docs, and one PR with CI green.

## Facts the design rests on

Read from the Tapir 1.7.1 source (`FavoritesCommands.cpp`, `ElementCommands.cpp` at tag 1.7.1) and
probed live on MCP-Test (Archicad 29 build 5101, Tapir 1.7.1, floor plan in front) on 19.10.2026 with
test elements near the origin.

### GetCollisions (1.2.2)

- Input: `elementsGroup1`, `elementsGroup2`, optional `settings` with all three of `volumeTolerance`
  (m3, default 0.001), `performSurfaceCheck` (default false), `surfaceTolerance` (m2, default 0.001).
  One call of `ACAPI_Element_GetCollisions`.
- Output: `collisions[]` of `{elementId1, elementId2, hasBodyCollision, hasClearenceCollision}`. Built
  against Archicad 30 the last key is spelt `hasClearanceCollision` (`#ifdef ServerMainVers_3000`), so a
  reader must take both spellings.
- Live: one group passed as both sides returned each colliding pair once, no self pairs, no mirrored
  duplicates (3 elements, 3 pairs). `elementId1` comes from group 1. A volume tolerance of 0.2 m3 dropped
  a 0.125 m3 overlap and kept a 0.25 m3 one, so the tolerance is the intersection volume in m3.
- Live: two crossing walls (joined by Archicad) gave no collision, even at tolerance 0 with the surface
  check on. Archicad does not count elements its own junctions clean up.
- Live: a Morph made by `CreateMorphs` with `size` is an open surface (5 faces, `isClosed` false, Tapir
  #729 still open) and collides with nothing under the volume check. A closed Morph body collided with
  the wall it crossed. In the live check the open surfaces did clash once the surface check was on.
  Archicad also leaves out building materials set not to take part in collision detection (its own
  setting, not tried live). The tool says all of this so a missing clash is not read as proof.
- No GUID check: an unknown GUID is simply never in a pair.

### GetFavoritesByType (1.2.2)

Names of the favourites of one element type (`ACAPI_Favorite_GetNum`). MCP-Test has 5 wall, 8 door, 6
window and 4 slab favourites. A favourite's type is only learned by asking per type.

### ApplyFavoritesToElements (1.5.4)

Per element: `ACAPI_Favorite_Get`, a type check (type id only), then `ACAPI_Element_ChangeParameters`
with a full mask and the favourite's memo (withheld for Stair, Railing and Curtain Wall, whose memo
carries geometry; their sub-element settings are not applied), then classifications
(`ACAPI_Element_AddClassificationItem` per item, errors ignored), categories and property values
(`ACAPI_Element_SetProperties`, errors ignored). Flags `applySettings`, `applyClassifications`,
`applyCategories`, `applyProperties`, all default true. One undo step for the whole call. Answers one
execution result per item.

Live on MCP-Test:

- A wall with ID `TEST-A`, layer "Shell - General", height 2.4 (top unlinked) and reference line Center,
  given "Partition Wall": ID became `SW - 001` (the favourite's own stored ID, not a new number), layer
  "Structural - Bearing", height 3 linked to the story above (`relativeTopStory` 1), reference line
  Outside, structure Basic 0.25 to Composite 0.1. Coordinates unchanged. So "never geometry" in Tapir's
  description is wrong for a wall's height and top link (Tapir issue #903, `keepFields` in open PR #905).
- A door given "Entrance Door" and a window given "Sliding Window": success, library part, width,
  height, sill and ID followed the favourite, `centerOffset` and the host wall unchanged. Tapir issue
  #812 reports `APIERR_REFUSEDPAR` (-2130312311) for openings; that did not happen on Archicad 29, but
  the code is translated if another version answers it.
- Wrong type: `-2130313115` "The Favorite's element type does not match the target element's type."
  Unknown favourite: `-2130313113` "Failed to get favorite".
- Writing the Element ID back works through the built-in property `General_ElementID` (official
  `SetPropertyValuesOfElements`, what `set_element_data` already does).

### ApplyFavoritesToElementDefaults (1.1.2)

Sets the tool defaults of the favourite's type from the favourite. Not probed live (it changes the
user's tool settings); the code path is three API calls with no traps in the source.

## find_collisions

`find_collisions(guids=None, element_types=None, against_guids=None, against_types=None,
volume_tolerance=0.001, surface_tolerance=None, port=None)`. Read-only hint. Full mode only (it returns
GUIDs and types, which verdicts mode never shows).

- **Groups.** The first group is `guids` plus every element of `element_types`; the second is
  `against_guids` plus `against_types`. Without a second group the first is checked against itself.
  Element types are matched to Tapir's `ElementType` names without case, plural `s` allowed; an unknown
  name is an error listing the valid ones. Elements of a type come from `GetElementsByType` (Tapir). My
  call: no property criteria in the tool itself; `find_elements` already answers those and its GUIDs
  pass straight in.
- **Settings.** `volume_tolerance` in m3 (Tapir's default 0.001). `surface_tolerance` in m2 turns the
  surface check on; `None` leaves it off. All three settings are always sent, as the schema requires.
- **GUID checks.** Explicit GUIDs are read with `GetDetailsOfElements` (fields `type`, `id`,
  `floorIndex`); unknown ones are listed in `not_found` and left out.
- **Result.** `{"checked": {"elements": n, "against": m or "the same group"}, "collisions": count,
  "pairs": [{"element", "element_type", "element_id", "against", "against_type", "against_id",
  "story", "kind"}], "by_type_pair": [{"types": "Morph / Wall", "count"}], "elements": [every GUID in
  a pair], "tolerances": {...}}`. `kind` is `body`, `clearance` or `body and clearance`. Pairs are
  capped at the report cap with `not_shown`; `elements` is capped the same way. Pairs are deduplicated
  as unordered pairs and self pairs dropped in case Archicad ever answers them for overlapping groups.
  `story` is the first element's `floorIndex`. My call: IDs come from the details read (no property
  read, so no crash risk).
- **No size ceiling.** MCP-Test holds three walls, so there is no timing to set one from. The
  description says a whole-model check can take a while.

### `no-collisions` rule type

```yaml
- id: beams-vs-ducts
  type: no-collisions
  element_types: [Beam, Column]
  against_types: [Object]        # optional; omitted = within element_types
  volume_tolerance: 0.001        # optional, m3
  surface_tolerance: 0.01        # optional, m2; turns the surface check on
  severity: error
  tags: [coordination]
```

The rule declares the need `collisions` and a `collision_query` (a frozen dataclass of the types and
tolerances). `build_snapshot` takes the queries of the rules being run and fills
`ModelSnapshot.collisions` (query -> pairs); it stays `None` when Tapir lacks `GetCollisions`, and the
rule then skips like the IFC rule does. Failing GUIDs are every element in a pair; `failures` is the
number of pairs; the message names the pair count, the element count and the types. My call: types
only in rules (no `where`), matching the tool.

## apply_favorites

`apply_favorites(guids=None, favorite=None, settings=True, classifications=True, categories=True,
properties=True, keep=["id"], to_defaults=False, dry_run=True, port=None)`. Destructive hint. Full mode.

- **Listing.** Without `favorite` it changes nothing and returns the favourites that fit the given
  elements' types (`{"favorites": {"Door": [...]}}`), or every type's favourites when no GUIDs are given
  either (types with none left out).
- **Plan (dry run and commit alike).** Deduplicate GUIDs (upper case); skip what Archicad would refuse
  (`core.editability.refusals`: other database, hidden or locked layer, Teamwork, hotlink, locked); read
  `type`, `id`, `layerIndex` of the rest; ask `GetFavoritesByType` once per type present. An element
  whose type lacks the favourite is skipped with "'X' is not a <Type> favourite", naming the type(s)
  that do have it. A favourite no type has is an error with close names (difflib).
- **What the dry run says.** Per element type, a warning naming what the favourite overwrites beyond
  materials and pens, from the live findings: walls take its height, top link, reference line and
  structure; columns its height and top link; doors, windows and skylights its library part, size and
  sill; stairs, railings and curtain walls only their own settings, not their parts'. Every element
  takes the favourite's layer and, unless kept, its Element ID. Classifications are added per system
  (systems the favourite does not classify stay as they are); property values the favourite stores
  overwrite the element's. Plus the four flags as sent.
- **keep.** My call: `keep` defaults to `["id"]`, because a favourite's ID is a template value (`SW -
  001`) and 40 doors with one ID break the numbering. Allowed: `"id"` (written back through
  `General_ElementID`) and `"layer"` (written back with `SetDetailsOfElements` `layerIndex`). Anything
  else is an error that names `modify_elements` for heights and sizes. When Tapir gets `keepFields`
  (#905) this maps onto it.
- **Commit.** One `ApplyFavoritesToElements` call for all planned elements, then the restores for
  `keep`, then a details read of the same fields as before (`type`, `id`, `layerIndex`, `drawIndex`,
  `details`). Result: `applied` (count), `changed` per element as `{field: {current, new}}` from a diff
  of the reads before and after (nested details flattened to dotted names, layer index and attribute
  GUIDs shown by name), `unchanged` for elements the favourite already matched, `failed` grouped by
  Archicad's code and message (the type mismatch and missing favourite codes and -2130312311 get a
  plain sentence), `restored` and `not_restored` for `keep`. Classifications and properties are not
  read back (property reads are the crash-prone class); the result says so.
- **to_defaults.** With `to_defaults=true` and no GUIDs, the dry run names the tool whose defaults
  change; the commit calls `ApplyFavoritesToElementDefaults`. Combined with GUIDs it is an error.

## Tapir requirements

`TOOL_COMMANDS`: `find_collisions` needs `GetCollisions` and `GetElementsByType`; `apply_favorites`
needs `GetFavoritesByType` and `ApplyFavoritesToElements` (1.5.4). Both are below 1.7.1, so no
definitions sync.

## Out of scope

Creating, renaming, importing or exporting favourites; property criteria inside `find_collisions`;
showing the clash (that is `show_elements` with `elements`); Tapir's `keepFields` until it is released.

## Testing

Unit tests with the `FakeCore` pattern: pair parsing (both clearance spellings, dedupe, self pairs),
group resolution, unknown types, tolerances sent, the rule through `build_snapshot` and `run_rules`
(skip without Tapir), favourite listing, type mismatch skips, editability skips, the warnings, keep
restores, the diff report, failure grouping. Then a live check on MCP-Test with test elements near the
origin, deleted afterwards.

## Live check

19.10.2026, MCP-Test, Archicad 29 build 5101, Tapir 1.7.1, floor plan in front. The branch's code was
run from a script against three walls, two doors, a window and four Morphs placed near the origin; all
ten were deleted afterwards.

`find_collisions`:

- Two walls against two closed Morph boxes: 2 pairs, both with the wall the boxes cross (the other wall
  is clear), kind `body`, IDs and story read.
- Morphs as one group: 1 pair, the two closed boxes. The two open surface Morphs gave none.
- Walls against Morphs at 0.2 m3: none (the wall was then 0.1 thick, an overlap of 0.1 m3).
- Walls against Morphs with `surface_tolerance` 0.01: 4 pairs, the two open surface Morphs included.
- A `no-collisions` rule, walls against Morphs, through `build_snapshot` and `run_rules`: fail, 2
  failures, 3 GUIDs.

`apply_favorites`:

- Listing for a wall and a door: the 5 wall and 8 door favourites.
- Dry run of "Generic Interior Wall" on two walls and a door: 2 planned, the door skipped as "not a Door
  favourite (it is a Wall favourite)", the wall and layer warnings, `keep` `["id"]`.
- "generic interior wall": refused, 'Generic Interior Wall' first among the close names.
- `to_defaults` dry run: named the Wall tool. The commit was not run live, since it changes the Wall
  tool's defaults.
- Commit on two walls with IDs `LIVE-A` and empty: applied 2; `changed` listed structure, thickness,
  composite and building material by name; `restored` id 2; the read showed `LIVE-A` and an empty ID,
  so writing an empty Element ID back works.
- "Internal Door" on a door with ID `D-LIVE`: library part "Entrance Door" to "Door", width 1 to 0.9,
  ID restored.
- A wall moved to "Shell - General" with height 2.4 (top unlinked), then "Generic Interior Wall" with
  `keep` `["id", "layer"]`: `changed` showed height 2.4 to 3 and `relativeTopStory` 0 to 1; `restored`
  id 1 and layer 1.
