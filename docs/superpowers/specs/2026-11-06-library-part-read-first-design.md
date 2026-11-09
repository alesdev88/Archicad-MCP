# Reading a library part before sampling it: design

Date: 06.11.2026. Builds on
[the learning design of 22.10.2026](2026-10-22-library-part-learning-design.md), whose
intent and anti-goals still hold. Depends on commands in Tapir's `main` (a7d4b075, labelled
1.8.1) that no release has yet. Nothing here has run in Archicad: facts about the new
commands come from Tapir's source and are marked where a live check must settle them.

## Intent

Unchanged: an agent rebuilding from a sketch gets the part, its size and its orientation
right the first time, and the user fixes nothing by hand afterwards.

Learning places samples, measures their plan boxes, shows them in one image and deletes
them. Two new reads change what can be known before and around that:

- `GetGDLParametersOfLibraryParts` gives a part's default parameters without placing it.
- `GetLibraryPart2DPrimitives` gives the lines, arcs and polygons of its 2D symbol, in the
  part's own coordinates, at its default parameters or those of a placed instance.

Neither says how a part changes with size at parameters no placed instance has, or what
`reflected` and `oSide` do to a door. Samples still answer those, so they stay. The user
chose this over learning with no samples, which would have guessed how a footprint scales,
and Bed Double 01 and Chair 01 are parts where that guess is wrong.

## What changes

### 1. Choosing a part: `preview_library_parts`

`preview_library_parts(names, port=None)` is a new read-only tool, in `full` mode only.

- It takes 1 to 8 part names. Any other count is an error, and nothing is read.
- For each name it reads `GetLibraryPart2DPrimitives` with `name` and
  `GetGDLParametersOfLibraryParts`, and draws the symbol into one image, each part in its own
  numbered cell at its own scale, with a cross at the insertion point. It places nothing.
- The reply is the image, then JSON: `parts` (`number`, `name`, `a`, `b`, `height` and
  `extent`, which is `[xMin, yMin, xMax, yMax]`), `not_drawn` (`name` and `reason`) and a
  `note`. Names stay out of the image, so no font has to carry č, š and ž; a cell shows only
  its number, drawn in Pillow's default font.
- A name Tapir does not find goes in `not_drawn`, with the closest loaded names from
  `core.create.closest_names`. A part whose symbol has no lines, arcs or polygons goes there
  too, with that reason.
- When nothing could be drawn, the reply is the JSON alone, with no image.
- On a Tapir without either command it refuses with `require_tapir_command`'s message,
  which carries the install hint.

The `note` says what the picture leaves out: it shows the default parameters, and only
lines, arcs and polygons, so no texts or hotspots. It helps an agent pick between candidates
and does not replace the look after placing. This removes the non-goal of 22.10.2026 that
no preview exists before placing.

The drawing is a pure function, `draw_symbols(symbols) -> bytes`, in a new module
`core/part_symbols.py`, beside `symbol_extents`. Pillow is already a dependency.

### 2. Sample sizes from the part

Learning reads the part's own A, B and ZZYZX first, for every kind. For an object or lamp,
the two sized samples are then the part's own size and 1.5 times it on both axes, and the
samples' height is its own ZZYZX. The mirrored sample and any variants take the first size, as
they do now.

These are the values stored in the part: Tapir says its Parameter Script is not run for this
read. A part that clamps its size may draw otherwise at them. Learning already handles that,
because `fit_axis` fits against what each sample took and read back.

It falls back to today's `SAMPLE_SIZES` and `SAMPLE_HEIGHT` when the command is missing, the
read fails, or A or B is missing or not above zero. `fit_axis` is unchanged: it already takes
whichever sizes the samples were placed at. A height that is missing or not above zero falls
back to `SAMPLE_HEIGHT` on its own.

Doors and windows keep their fixed sample sizes, as their samples are not fitted; they read
their own size only for the record.

### 3. The part's own size in the record

`PartRecord` gains `own_size`: `{"a": float, "b": float, "height": float | None}`, or `None`
when it was not read. `from_json` already tolerates its absence, so older records load as they
are. Learning writes it for every kind. Learning again replaces it when the read succeeds and
keeps the earlier one when it does not, as the GUID check has already said it is the same part.

**In `create_elements`**, for an object or lamp, `placement.size` becomes optional:

- Without `size`, the footprint is worked out at the part's own A and B, with length and width
  following the record's head direction (`object_fields` already holds this rule), and the item
  sends them explicitly as `dimensions`. This gives the part its own size without waiting for
  Tapir PR 1395, and stays right once it ships, as explicit dimensions still win.
- Without `size` and without `own_size`, the item is skipped with the reason: give `size`, or
  learn the part again on a Tapir newer than 1.8.0.
- `size.height` falls back to `record.height`, as now, then to `own_size.height`. With neither,
  the item is skipped. `NO_HEIGHT` stops blaming Tapir 1.7.2 and says this record holds no height.

For a door or window with `placement`, an item that gives no `width` fills it from `own_size.a`,
and one that gives no `height` fills it from `own_size.b`. Tapir's schema says these are the
opening's A and B. Without `own_size` the field stays out and the tool's value applies, as now.
`sillHeight` is untouched: a part's defaults do not say where it sits in a wall.

Whatever is filled in shows in the reply's `placements[].derived`, as everything else a placement
works out does, so it is not a side effect kept quiet. The size asked of a part that draws otherwise
gets the existing `SIZE_TOLERANCE` warning.

**In `modify_elements`**, `placement.size` stays required. An existing element has a size, and
leaving it out would have to mean "keep it" there and "the part's own" in `create_elements`.
The shared schema therefore needs a variant, as `OBJECT_PLACEMENT` serves both today.

### 4. A symbol check, not a new measurement

After the sized samples are placed, learning reads `GetLibraryPart2DPrimitives` with `name` and
`elementId` for each, and `symbol_extents` turns the answer into `(xMin, yMin, xMax, yMax)` in the
part's coordinates. Each is compared with that sample's plan box from the insertion point.

- A difference above `SIZE_TOLERANCE` (0.03 m) on any edge adds a warning naming the sample and
  both boxes.
- The reply gains `symbol_check`: `"differs"` when any sample read differs, `"agrees"` when every
  sample read agrees, and `"unread"` when no sample's symbol could be read. It is left out on a
  Tapir without the command.
- A failed read of one sample adds a warning and does not stop learning.
- The mirrored sample is not compared. "The coordinate system of the library part" does not say
  whether `reflected` is applied, and the footprint is not fitted from it.

**Plan boxes stay the measurement.** A plan box may hold what the symbol read leaves out, so some
differences may be harmless, and that is not known yet. Measuring from symbols is a separate
decision for after the live check.

`symbol_extents(primitives)`:

- A `Line` counts its two ends.
- A `Polygon` counts its `coordinates`. Its holes lie inside the outline, so they add nothing.
- An `Arc` with `isWhole` counts `origin` plus and minus `radius` on both axes.
- Any other `Arc` counts its two end points, and each axis extreme whose angle lies on the way
  from `begAngle` to `endAngle`, taken counter-clockwise. The schema does not state the direction,
  so the live check must confirm it; being wrong here produces a warning, not a wrong measurement.
- No primitives gives `None`.

### 5. What learning says about itself

- The warning that the Door or Window tool now holds the part appears only where
  `create._part_name_faults_tool` says the fault exists: Tapir 1.8.0 and older, or a Tapir that
  does not say. The same goes for the wording of the parameter warning.
- When a door's or window's sample cannot give its parameters, the reply's `parameters` come from
  `GetGDLParametersOfLibraryParts` if that answers, and a warning says they are the part's
  defaults, not the sample's. An agent can still read which parameter opens which leaf.
- The `learn_library_part` tool description and `docs/tools.md` describe the door and window fault
  as belonging to Tapir 1.8.0 and older, where they now name only 1.7.2, and mention the new reads.

## Older Tapir

Each new command is checked with `conn.tapir_command_available`. Without them, `learn_library_part`
sends exactly the commands it sends today, and a test pins that list against a Tapir 1.8.0 fake.

## Non-goals

- Learning with no samples.
- Measuring from symbols. It needs the live check first.
- Hinge and swing from a door's arcs. The arcs would give them in the part's own axes, but the
  table of what `reflected` and `oSide` do is still something a sample shows.
- Keyword search of the library (`GetLibraryPartMetadata`). Name filtering serves meanwhile.
- A `create_elements` warning that quotes the own size of a part that was never learned.
- Changing the CLI.

## Anti-goals

From 22.10.2026, unchanged: a sample left in the model, a sample that disturbs the drawing, a side
effect kept quiet, a wrong orientation reported as done, a record trusted for another part, slowness.

New here:

- A check that overrules the measurement before it has been seen to agree.
- A preview taken as proof. The look after placing stays.
- A record that silently changes a placement that worked: a `placement` that gives `size` sends
  exactly what it sends today.

## Decisions

- **Samples stay.** The user chose reads around them over reads instead of them.
- **Sizes of 1 and 1.5 times the part's own.** Fitting near the real size suits a part that clamps,
  and a small part is no longer sampled at 2 m. The 1.5 is a choice, not a measured value; any
  factor that varies the size enough for `fit_axis` serves.
- **One sheet per call, 8 parts.** An agent comparing candidates wants them together. Eight keeps
  the image legible and the reads few, within the speed limits of 22.10.2026. Each cell has its own
  scale, because a stool and a bed on one scale would make the stool unreadable; sizes come in the JSON.
- **`own_size` as a new field**, kept apart from `height`. `height` is what was recorded about a
  part, and may be a deliberate choice; `own_size` is what the part says, read fresh on each learning.
- **A door's `width` filled from A, a window's too, and `height` from B.** Tapir's schema says these
  are the same values for doors and windows placed by part name. Read from the source, so the live
  check confirms it.
- **No new command of our own**, as on 22.10.2026.

## Constraints

- A verdict and a rule are not involved, so the privacy guard on `RuleResult` does not apply. The
  tool returns part names, which are library data, and is `full` mode only.
- Read commands only in `preview_library_parts`. Its annotation is read-only and non-destructive.
- `TOOL_COMMANDS` gets `preview_library_parts`, so `tapir_refusal` and the dashboard see it.
- The transport raises on a top-level `error`, so failures of these reads arrive as
  `TapirCommandError`. A per-item failure arrives as an item holding `error`, which the code must
  also treat as a failure.
- `GetGDLParametersOfLibraryParts` finds a part through its document name, the same name
  `CreateObjects` takes, and `GetLibraryPart2DPrimitives` by "the name of the loaded library part"
  through its own lookup. Whether they match on every part is for the live check.
- Nothing in the two commands' source limits them to an Archicad version, so no version gate is
  added. The live check runs on Archicad 29 only, so older versions stay unconfirmed.

## Testing

Test first, against the existing fakes (`tests/test_part_learning.py`, `tests/test_part_placement.py`,
`tests/test_part_records.py`, and `tests/test_create_elements.py` for `placement`).

- `symbol_extents`, table-driven with hand-worked values: a line; a polygon with a hole; a whole arc;
  arcs that cross 0, pi/2, pi and 3 pi/2 and ones that cross none; an arc past 2 pi; a mix; nothing.
- `draw_symbols`: that it gives a PNG of the expected size and that a cell holds dark pixels. Not its
  exact pixels.
- Learning on a Tapir newer than 1.8.0: samples at the own size and 1.5 times it; the fallbacks for a
  missing command, a failed read and a zero size; `own_size` written, replaced, and kept on a failed
  read; `symbol_check` agreeing, differing and unread; the opening's parameters falling back.
- Learning on a Tapir 1.8.0 fake: the command list is unchanged, and no `symbol_check` or `own_size`.
- `create_elements` placement: without `size`, along each head direction; with `size`, an unchanged
  payload; without `size` or `own_size`, a skip; the three steps of `size.height`; a door's `width` and
  `height` filled, kept when the item gives them, and shown in `placements[].derived`.
- `modify_elements`: a placement without `size` is still refused.
- `preview_library_parts`: its limits, a part not found, a part with nothing to draw, nothing drawn at
  all, a Tapir without the commands, and that it sends no write.

## Live check

Once a newer Tapir is installed, on MCP-Test only:

1. Learn Bed Double 01, Chair 01, Door and Window. Compare the symbol's extents with the plan boxes:
   that comparison decides whether measuring from symbols is ever worth doing, and it settles the arc
   direction.
2. `preview_library_parts` on those four: drawn the right way up, the cross where the insertion point is.
3. Place a bed and a chair by `placement` without `size`: the own size, and which way the head points.
4. Place a door without `width`, with the Door tool set to another width: the part's width, not the tool's.
5. Whether the two reads find the same part under the same name.

Add these to `docs/known-issues.md`, section "Tapir after 1.8.0", when this is built.

## Left to the builder

- The cell size and margins of the sheet.
- The exact wording of warnings and the `note`.
- Whether `symbol_extents` and `draw_symbols` take the raw primitives or a parsed form.
- How the `modify_elements` schema variant is built from `OBJECT_PLACEMENT`.
