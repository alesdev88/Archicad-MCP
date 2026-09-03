# Finding elements

Two tools, one language. `search_definitions` tells you what a property is
called and whether you can write it. `find_elements` finds the elements that
match criteria over those properties, their classification, their story and
their type. Both are full-mode tools.

This replaced `query_elements` in 0.4.0. The old tool took five AND-combined
filters and could not express "walls whose fire rating is empty", which is the
question every delivery check starts from. There is no compatibility shim: the
repo keeps few, sharp tools, and two overlapping query tools is the opposite.

## What Archicad can and cannot filter

Live-probed on Archicad 29 build 5101 with Tapir 1.5.9 on 03.09.2026, across
the 311 commands in the bundled catalog:

| Question | Answer |
|---|---|
| Does any command filter elements by property value server-side? | **No.** Tapir `FilterElements` knows 13 flags (visibility, editability, floor, workspace). The official `GetElementsByClassification` matches one exact item. |
| Where do property values come from? | `GetPropertyValuesOfElements`, the command that can crash Archicad ([known issues](known-issues.md#reading-property-values-can-crash-archicad)). |
| Can element types be narrowed server-side? | Yes, Tapir `GetElementsByType`, one call per type. |
| Can a property's availability be known without reading values? | For user-defined properties, yes: `GetPropertyDefinitionAvailability` lists the classification items the definition applies to, expanded to every descendant. Checked against 228 custom definitions on one wall: 0 disagreements with the wall's own available-property list. Built-ins report nothing here. |
| Where do property definitions come from? | Tapir `GetAllProperties`: 1619 built-in definitions plus the custom ones, with group, name, types, editability and enum values, in one call. |
| Are `Group/Name` pairs unique? | Not for built-ins (11 duplicate pairs live). User-defined pairs were unique. |

So `find_elements` evaluates property criteria **in the server**, after reading
the values, and everything about its design is about reading as little as
possible. See "What it reads" below.

## `search_definitions`

```
search_definitions(query, kind="any", alternatives=None, editable_only=False, limit=25)
```

Fuzzy, case- and accent-insensitive search over property definitions and
attribute names. Every query word must match somewhere in the group, name, API
name or enum values; whole-word matches rank above prefixes, prefixes above
substrings, and a one-letter typo still finds the definition.

- `kind`: `property`, `attribute` (layers, lines, fills, composites, surfaces,
  layer combinations, zone categories, profiles, pen tables, building
  materials), or `any`.
- `alternatives`: up to six synonyms or translations, each searched as its own
  query. On a Slovenian project "fire rating" finds nothing, "požarna odpornost"
  does; pass both.
- `editable_only`: keep only properties whose value can be written on at least
  one element type. This is what to check before `set_element_data`. `true`
  does not promise every element accepts a write.

A property match:

```json
{"kind": "property", "name": "OFFICE/Fire Rating", "property": "OFFICE/Fire Rating",
 "group": "OFFICE", "builtin": false, "value_type": "String", "measure_type": "Default",
 "collection": "Single", "editable": true, "expression_based": false,
 "guid": "89B25A85-...", "score": 1.0}
```

`property` is the address `find_elements`, `get_element_data`,
`set_element_data` and rules accept. For a user-defined property it is
`Group/Name`. For a built-in it is the API name (`ModelView_LayerName`) when the
official API has one, and otherwise the definition's GUID, which every tool also
accepts. `measure_type` says which SI unit a numeric value is in.

An attribute match carries `attribute_type` and `name`. `list_attributes`
remains the exact listing of one type.

The tool reads definitions only. It never calls `GetPropertyValuesOfElements`.
Without Tapir it falls back to the official API and says so in `notes`; the
fallback lacks measure types, collection types and enum values.

## `find_elements`

```
find_elements(groups, selection_only=False)
```

`groups` is a non-empty list. Groups combine with **OR**. Inside a group:

| Field | Default | Meaning |
|---|---|---|
| `element_types` | every type | Archicad type names: `Wall`, `Slab`, `Zone`, `CutPlane`, ... `"all"` explicitly means every type. |
| `element_types_operator` | `is` | `is_not` keeps elements whose type is none of the listed ones. |
| `logical_operator` | `and` | How the group's comparisons combine. |
| `comparisons` | `[]` | List of `{property, operator, value}`. |

A group with `element_types` and no comparisons lists that type. `selection_only`
restricts every group to the current selection (including markers, which the
official selection command cannot see).

### Addresses

`property` is one string, in one of three shapes:

| Shape | Addresses | Example |
|---|---|---|
| `Group/Name`, an API name, or a GUID | A property. `search_definitions` hands these out. | `OFFICE/Fire Rating`, `ModelView_LayerName` |
| `classification:<System name>` | The element's item in that classification system. Values are item IDs as Archicad shows them (`Wall`) or item GUIDs. | `classification:Archicad Classification` |
| `story` | The home story, as Archicad's story index (Tapir `floorIndex`): ground floor `0`, basements negative, upper floors positive. Read the indices with `get_project_info`. | `story` |

### Operators

| Operators | Apply to | Notes |
|---|---|---|
| `equal`, `not_equal` | everything | Strings compare case-insensitively. Numbers compare with a tolerance, so `3` equals a wall measured at 2.9999999999. A list (multi-choice enum, string list) equals a scalar when any member does. |
| `less`, `greater`, `less_or_equal`, `greater_or_equal` | numbers, strings, `story` | Strings order lexicographically, case-folded. A number never orders against a string. |
| `contains`, `does_not_contain`, `starts_with`, `ends_with` | strings, string lists, enums | Case-insensitive. On a list, `contains` matches if any member does; `does_not_contain` needs every member to miss. |
| `is_in_branch_of`, `is_direct_child_of`, `is_not_in_branch_of`, `is_not_direct_child_of` | `classification:` only | Tested against the system's tree. An item is in its own branch. |
| `has_value`, `has_no_value` | everything | A **usable** value: status normal and not empty (`""`, `[]`). |
| `is_user_undefined`, `is_not_user_undefined` | everything | The property exists on the element and was explicitly left undefined. |
| `available`, `not_available` | everything | Whether the property exists for this element at all. |

Those last three pairs are the four senses of "empty" a property cell can have
in Archicad, and they differ: an unclassified wall's fire rating is
`not_available` when the property is scoped to a classification, and
`is_user_undefined` when the property applies but nobody filled it in. A
delivery check usually wants `has_no_value`, which is all of them together.

**An element with no usable value matches no binary operator.** `not_equal`
and `does_not_contain` included. Ask `has_no_value` for those elements.

### Values and units

Numeric values are in SI base units, the units the JSON API itself uses:

| `measure_type` | Unit |
|---|---|
| Length | m |
| Area | m² |
| Volume | m³ |
| Angle | radian |

Convert before calling: 3000 mm is `3`, 45° is `0.7854`. Booleans are `true` /
`false`. Enum values are their display text. Story values are integers.

### Result

```json
{"count": 12, "guids": ["..."], "by_type": {"Wall": 12},
 "candidates": 140, "property_reads": 140, "skipped_not_available": 3,
 "coverage": "whole-plan", "notes": []}
```

- `candidates`: how many elements the type filters left for the criteria to test.
- `property_reads`: how many elements were sent to `GetPropertyValuesOfElements`.
  This is the number to watch.
- `skipped_not_available`: (element, property) pairs answered as `not_available`
  from the definition's availability, without a read.
- `notes`: an address that did not resolve, a classification system that does
  not exist, or a story comparison without Tapir. Each one names what it
  affected; a silent zero would be worse.
- `coverage`: as everywhere else, `model-elements-only` without Tapir means 2D
  elements are invisible and a count of 0 is not proof of absence.

### What it reads, in order

1. **Types**, server-side. A group naming types costs one Tapir call per type
   and reads nothing else. `is_not` and `all` enumerate the plan and read every
   element's type, which is cheap and has never crashed anything.
2. **Story and classification**, for the candidates that need them. Cheap
   reads, and in an `and` group they run first: an element failing a story or
   classification comparison is dropped **before** any property is read.
3. **Availability**, for user-defined properties: which classification items
   each definition applies to, matched against each candidate's
   classifications. A pair the definition does not cover is answered
   `not_available` and is never sent to Archicad.
4. **Property values**, for what is left, chunked and subject to the
   [element ceiling](known-issues.md#the-element-ceiling-is-blast-radius-control-not-a-fix).
   Elements that can take different subsets of the requested properties are
   read in separate requests so no request asks for a pair the definition
   excludes.

Steps 3 and 4 are aimed at the hypothesised crash trigger, a property read on an
element the property does not apply to. Every recorded crash was a
user-defined property read, and the pre-check predicted availability correctly
on every definition it was checked against. It is bounding, not proof: built-in
properties have no such pre-check, and the ceiling remains the last line.

Practical consequence: **name element types**. `{"comparisons": [...]}` with no
types tests the whole plan; on a 60k-element project that is refused by the
ceiling, and rightly so.

### Examples

Walls with no fire rating, on any story:

```json
{"groups": [{"element_types": ["Wall"], "comparisons": [
  {"property": "OFFICE/Fire Rating", "operator": "has_no_value"}]}]}
```

External walls taller than 3 m, or any curtain wall:

```json
{"groups": [
  {"element_types": ["Wall"], "comparisons": [
    {"property": "OFFICE/Wall Type", "operator": "equal", "value": "External"},
    {"property": "11111111-1111-1111-1111-111111111111", "operator": "greater", "value": 3}]},
  {"element_types": ["CurtainWall"]}]}
```

Everything classified under Site that is not a mesh:

```json
{"groups": [{"element_types": ["Mesh"], "element_types_operator": "is_not", "comparisons": [
  {"property": "classification:Archicad Classification", "operator": "is_in_branch_of", "value": "Site"}]}]}
```

Selected elements on the ground floor (story index 0) whose layer starts with `A-`:

```json
{"groups": [{"element_types": ["all"], "comparisons": [
  {"property": "story", "operator": "equal", "value": 0},
  {"property": "ModelView_LayerName", "operator": "starts_with", "value": "A-"}]}],
 "selection_only": true}
```

## The same language in rules

`applies_to.where` in a [rule](rules.md#applies_towhere) takes the same
comparisons, so a scope found with `find_elements` becomes the scope of a check
without translation. The evaluator lives in `archicad_mcp.criteria`, which
imports no transport, so the rules engine stays importable on its own.

## Not copied from Graphisoft's criteria_evaluator

- `has_default_value` / `has_custom_value`: no API cell says whether a value is
  the definition's default. Left out rather than guessed.
- Element sets as opaque references: this server returns GUIDs, which is what
  every other tool here consumes.
- A required list of alternative search terms: `alternatives` is optional.
