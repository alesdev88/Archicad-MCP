# Schedule Scheme Editing: Design

**Date:** 2026-07-28
**Status:** Approved, not yet implemented.
**Repo:** https://github.com/alesdev88/Archicad-MCP.git

## Purpose

Let an MCP client read, edit, and validate Archicad Interactive Schedule
schemes: the criteria that decide which elements a schedule catches, and the
columns that decide what it shows.

## Why this needs a file-based design

Schedules are the one part of Archicad with **no programmatic interface at
all**. This is not a JSON API gap that Tapir might close later:

- All 231 gateway commands (73 official + 158 Tapir, definitions synced
  2026-07-24 at Tapir 1.5.5) contain nothing that reads or writes a schedule.
  There is no `GetSchedules`, no scheme, no criteria, no fields.
- Graphisoft's developer forum answers state there is no API functionality
  relating to schedules and that they support no developer interaction, in the
  context of the **C++ API**. A custom compiled add-on would not reach them
  either.
- The only schedule-shaped thing in the entire surface is `ChangeWindow` with
  `windowType: "Interactive Schedule"`, which switches the active window and
  nothing more.

What Archicad *does* support is a documented XML round trip. Scheme Settings
(Document > Schedules > Scheme Settings) has Import and Export buttons. Export
writes a scheme as XML; Import reads it back, and accepts several schemes at
once. That file is the seam this design works through.

The user clicks Export, the MCP edits the XML, the user clicks Import. Two
manual steps per round trip, accepted as the tradeoff for a deterministic,
testable, versionable edit path.

### Rejected alternative: GUI automation

Driving the Scheme Settings dialog directly (computer use or UI scripting)
would remove the two clicks and edit in place. Rejected because it is
locale-dependent (this office runs bilingual Slovenian/English captions),
breaks on any interface change, cannot be unit tested, is macOS-only in
practice while this server supports Windows, and produces exactly the kind of
unverifiable write the rest of this repo avoids. It stays available as a later
add-on over the XML layer if the manual steps become a real problem.

## The format, as verified

Verified against two real office schemes (a 27-column door schedule and a
21-column window schedule), both `Version="29.0.0"`.

Root is `Scheme_Settings` carrying `ID`, `Last_Modified`, `Name`,
`Scheme_Type` (`Element_List`), and `Version`. Six sections follow:
`View_Settings`, `Criteria_Settings`, `W2D_Settings`, `Header_Items`,
`DimensionSettings`, `FieldCustomDataStore`.

Three findings drive the design.

**Columns are a linked-list tree, not a list.** Each `Header_Item` carries
`ID_of_Item`, `ID_of_Parent`, `ID_of_firstChild`, `ID_of_previous`, and
`ID_of_next`. The door scheme's 28 `Header_Item` nodes are one root (parent 0,
caption equal to the scheme name, holding `Numbers_of_Columns="27"`) plus 27
column nodes chained as siblings. Adding a column means splicing a doubly
linked list, allocating a fresh `ID_of_Item` and `UniqueID`, and updating the
root's count. Appending an XML node produces a corrupt scheme.

**Columns bind to data in three different ways**, and each needs separate
handling:

| Binding | Encoded as | Example from the door scheme |
|---|---|---|
| Archicad property | `ACPropertyGuid` | the door ID column |
| GDL library parameter | `ACPropertyName` + `Parameter_Desc_Name`, with `Parameter_Type=180`, `Parameter_Index=-1604` | 20 of the 27 door columns |
| Built-in field | `Parameter_Type` + `Parameter_Index` | the quantity column is type 1, index -1003 |

**Criteria are undocumented numeric codes plus GUIDs.** The door scheme is
three criteria: `Param_Type=88` with `Relation_Index=1` matching element
classification GUID `D8F07689…` (Door), then two `Param_Type=232` with
`Relation_Index=12` against property GUID `432FA53A…`. Chaining is
`AndNext`. No public documentation of these code tables exists.

### Round-trip fidelity is provable

Parsing both real schemes with `xml.etree.ElementTree` and reserializing
reproduces the original **byte for byte**, given three serializer details:

1. Emit `<?xml version="1.0" encoding="UTF-8" standalone="no" ?>` verbatim
2. Emit self-closing tags as `/>` with no preceding space
3. Emit one trailing newline

This is the foundation of the whole design and the first test to write.

### Incidental finding

In the schemes examined, a column captioned as a fire resistance rating turned
out to be bound to a GDL parameter whose own name ended in "not in use". The
caption and the binding disagreed, so the column had been reporting something
other than what it claimed. This surfaced from parsing alone, and it is the
motivating case for `validate_schedule_scheme`.

### Related format, out of scope

`vrata.xml` in the same template folder is not a scheme. It is a property
definitions export rooted at `BuildingInformation/PropertyDefinitionGroups`,
the Property Manager's own round trip. Noted because the same
export/edit/import shape applies, but property definitions already have API
commands (`CreatePropertyDefinitions`, `UpdatePropertyDefinitions`), so they do
not need this treatment. Out of scope here.

## Core principle: preserve what we do not understand

Load a real exported scheme, mutate only `Header_Items` and
`Criteria_Settings`, and pass `View_Settings`, `W2D_Settings`,
`DimensionSettings`, `FieldCustomDataStore`, and every unrecognised element and
attribute through untouched. An edit can then never corrupt a part of the
format that has not been reverse-engineered. The byte-exact round-trip test
enforces this: a no-op edit must produce an identical file.

This also decides the generation strategy. Schemes are **edited from a
template**, never synthesised from scratch, because the sections we do not
model still have to be present and valid.

## Architecture

```text
MCP layer          3 tools in core/schemes.py, full mode only
    ↓
schemes/           pure library: parse, model, edit, serialise
                   no MCP imports; only validate.py touches the API
    ↓
Transport          multiconn_archicad, for binding validation only
```

```
src/archicad_mcp/schemes/
    __init__.py
    model.py      Scheme, Column, Criterion over the tractable subset
    xml_io.py     load and save at byte-exact fidelity
    columns.py    linked-list splice: add, remove, move, rename, retarget
    criteria.py   Param_Type and Relation_Index tables, criterion read/write
    spec.py       YAML spec loader, applied onto a template scheme
    validate.py   resolve bindings against a live model
core/schemes.py   thin MCP-facing wrappers, matching the other core modules
```

`schemes/` imports nothing from MCP or transport code, the same seam
`rules/` already holds. Only `validate.py` reaches the API, and only through
`GetAllProperties`, which reads property *definitions* and therefore does not
touch the `GetPropertyValuesOfElements` crash path documented in
[known issues](../../known-issues.md).

## Tool surface

Three tools, registered in `full` mode only.

| Tool | Does | Touches Archicad |
|---|---|---|
| `read_schedule_scheme` | Parse an exported XML and describe it in plain language: scheme type, criteria, ordered columns, and what each column binds to | no |
| `edit_schedule_scheme` | Apply a YAML spec to a scheme: add, remove, reorder, rename, retarget columns and change criteria. Dry run by default, returns a before and after column table | no |
| `validate_schedule_scheme` | Check a scheme against the open model: do property GUIDs resolve, do GDL parameter names exist, does any caption disagree with its binding | yes, definitions only |

Cloning a scheme across element types needs no fourth tool. A spec names a
template and overrides its criteria and columns, so producing a window schedule
from a door schedule is one spec file.

## The YAML spec

Mirrors the existing rules YAML, with the three binding kinds the format
actually uses:

```yaml
- id: door-schedule
  template: exports/door-scheme.xml
  name: "Door Schedule"
  criteria:
    - element_class: Door
    - property: "OFFICE/Fire Rating"
      relation: not-empty
  columns:
    - caption: "Door ID"
      bind: { property: "OFFICE/Door ID" }
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating" }
      width: 30
```

A spec is declarative: the listed columns become the scheme's columns, in that
order. Column properties not mentioned (widths, fonts, totals, background
colours) are inherited, so a spec never has to restate formatting it does not
care about. Inheritance resolves in this order:

1. The template column with the same `caption`, if one exists
2. Otherwise the template's first **column** node, meaning the root
   `Header_Item`'s first child, never the root itself
3. Otherwise, for a template with no columns, documented built-in defaults

## Safety

Consistent with the rest of the server:

- **Dry run by default.** `edit_schedule_scheme` reports the before and after
  column table and writes nothing unless told to.
- **Never overwrite the input.** Edits go to a new path.
- **No live model required** for reading or editing. Only `validate` needs
  Archicad open.

## Build order

Sequenced so that everything shippable ships before the one piece blocked on
research.

1. **Round-trip fidelity, model, and `read`.** The foundation. Provable today
   against the two real schemes.
2. **Column operations.** Add, remove, reorder, rename, retarget: the
   linked-list splice work.
3. **YAML spec and apply-onto-template.** Cloning across types falls out of
   this nearly free.
4. **`validate` against the live model.**
5. **Criteria editing.** Last, because it is the only piece that cannot start
   until the code tables exist.

## Open questions

**Does Import update in place or create a duplicate?** Graphisoft's
documentation says duplicate names are auto-numbered on import, but both real
schemes carry stable IDs (2001, 2301) that suggest an in-place match may be
possible. This decides whether the workflow is "edit and reimport" or "edit,
reimport, delete the original, rename". Settle it empirically on a scratch
project before step 3. It does not block steps 1 and 2.

**The `Param_Type` and `Relation_Index` code tables.** Undocumented, and
required for criteria editing. Method: on a scratch project, set one criterion
at a time in the Scheme Settings GUI, export after each, and diff the exports.
An estimated 10 to 15 exports covers the common cases (element type, layer,
property comparisons, classification). Until the table exists, criteria are
read and preserved but not edited.

## Testing

Offline, with no running Archicad, for everything except `validate`.

- **Byte-exact round-trip is the primary regression guard.** Parse, serialise,
  compare: identical. A no-op edit must not change one byte.
- **Structural invariants after every column operation:** the sibling chain is
  intact in both directions, `ID_of_firstChild` matches the actual first child,
  `Numbers_of_Columns` matches the real count, every `ID_of_Item` and
  `UniqueID` is unique.
- **Fixtures are anonymised.** The real door and window schemes are office
  standards and this repo is going public. Test fixtures reproduce the
  structural shape (the same linked-list topology, all three binding kinds,
  both criterion `Param_Type` values seen) with invented captions and property
  names. The real schemes stay local-only validation, the same way
  `ARCHICAD_MCP_RULES_DIR` keeps office rules out of the repo.

## Out of scope

- Reading schedule *output* (the rows a schedule produces). No API returns it;
  the route would be publishing to XLSX through a Publisher Set.
- Writing element values through a schedule. `set_element_data` already covers
  this.
- Creating a schedule as a Navigator item. No API command exists.
- GUI automation of the Export and Import clicks.
- Property definition XML (`BuildingInformation`), which has API commands
  already.
