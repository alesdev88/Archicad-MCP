# Property reads through Tapir's own command: design

Date: 11.11.2026. Follows [the pre-check spike of 11.11.2026](../../known-issues.md), which
found that Tapir's `GetPropertyValuesOfElements` reads pairs the official command refuses.
Every fact below marked "live" was run on MCP-Test (Archicad 29 build 5101, stock Tapir 2.0.0)
on 11.11.2026. Nothing here is built.

## The decision

Do not move property reads to Tapir. Keep the official `API.GetPropertyValuesOfElements` as
the read, and add Tapir's command as a **fallback for the two gaps the official read has**:

1. elements it refuses whole (every 2D element: error 7203, "Element not supported"), and
2. definitions it refuses on every element (890 of 1856 in MCP-Test: error 6702, "Property
   definition not supported").

Writers stay on the official read alone.

## Intent

A read tool tells the truth about a property the first time it is asked. Today it does not
in two cases, and it does not say so:

- `find_elements` with `General_Locked equal false` on the two labels and two polylines of
  MCP-Test, none of them locked, matches none (live). The official read refuses each element
  and `fetch_property_cells` turns that into "not available".
- `find_elements` with `not_available` on General Parameters / Layer matches both walls
  (live), though each wall has a layer. The same holds for Building Materials (All) and
  Archicad IFC ID, by the same path in the code.
- `lock_reasons` reads the lock the same way, so by the code it cannot name the lock on a
  locked 2D element, and `move_elements` and `delete_elements` report it as "otherwise
  locked". Not run live: nothing in MCP-Test is locked.

Success is that these three answer correctly on Tapir 2.0.0 or newer, and that without it
the tools say which properties and elements they could not read instead of calling them not
available.

## Why not all reads

Tapir's command answers in a form the server cannot use as its only source (all live):

- **Every value is text.** A cell is `{"propertyValue": {"value": "<text>"}}` with no `type`
  and no `status`. By default the text is formatted for display in the project's units and
  locale: a 3 m length is `"3,00"`, a right angle `"90°"`.
- **`useSIUnits` makes numbers exact** (`"0.29999999999999999"`, `"11.890408816319191"`), but
  angles come in degrees where the official read gives radians, and the option exists only
  from Tapir 2.0.0.
- **`userUndefined` is the text `"<Undefined>"`**, not a status.
- **Lists are one text**, items joined with `"; "` (`"51; 59"`), so a text item containing
  `"; "` cannot be told apart.
- **Enumerations are their display text**, without the id the official read carries.
- **Writing needs the typed cell.** `API.SetPropertyValuesOfElements` takes a fully typed
  value, which `plan_property_writes` builds from what the official read returned.
- **The server works without Tapir today**, and would not if this were the only read.

On the six model elements of the spike the official read answered 5796 pairs with a status,
and Tapir agreed on every one, so the official read loses nothing by staying first.

## What Tapir's command gives the fallback

All live, with `includeAvailability: true` and `useSIUnits: true`:

| Official answer | Tapir answer | `isAvailable` |
|---|---|---|
| value, status `normal` | value | true |
| status `userUndefined` | value `"<Undefined>"` | true |
| status `notEvaluated` | error "Not available or not evaluated property" | true |
| status `notAvailable` | the same error | false |

So an error with `isAvailable` true is `notEvaluated`, and with false it is `notAvailable`.

- **One call takes several elements.** An unknown element answers an error for that element
  alone (code -2130313115) and the others are read.
- **One unknown property id fails every element in the call** (code -2130313215, "Failed to
  get property values for element"). The fallback must never send an id it has not seen in
  the project's definitions.
- **What it read that the official command refused:** on the wall, slab and object 395 pairs
  of the unsupported definitions, declared as text (249), integer (72), text list (30),
  integer list (29), GUID (12) and single-choice enumeration (3). On the label 140 pairs.
  The other unsupported pairs are simply not available on those elements.

## What changes

### 1. `extract.fetch_property_cells` learns the gaps and can fill them

It keeps its signature and gains one keyword, `tapir_fallback: bool = False`.

- It stops discarding the official error. A cell refused with 6702 and a row refused with
  7203 are recorded as gaps instead of becoming `{}`.
- With `tapir_fallback` false, or when the fallback cannot run, the gaps become cells with
  `{"status": "notRead", "reason": ...}`, where the reason is one of the two official
  messages. `Cell.from_api` maps `notRead` to its own status (see 3).
- With it true and Tapir 2.0.0 or newer, the gaps of one chunk are read in one Tapir call:
  the elements that have a gap against the properties that have one, with
  `includeAvailability` and `useSIUnits`. Cells the official read already answered are not
  overwritten. A Tapir error for a whole element leaves that element's gaps `notRead`.
- The element ceiling and the chunk size apply to the fallback as they do to the read. It is
  a property read like any other, and whether it shares the Archicad abort is not known.

### 2. Tapir text becomes a typed cell: `extract.tapir_cell`

A new pure function, `tapir_cell(raw, definition) -> dict`, turns one Tapir answer into the
cell shape the rest of the server already reads (`status`, `type`, `value`), using the
definition's declared type from `GetAllProperties` (`propertyValueType`,
`propertyCollectionType`, `propertyMeasureType`). It adds `"via": "tapir"`.

| Declared | Cell `type` | Value |
|---|---|---|
| Real, measure Length, Area, Volume or Default | `length`, `area`, `volume`, `number` | `float(text)` |
| Real, measure Angle | `angle` | degrees to radians |
| Integer | `integer` | `int(text)` |
| Boolean | `boolean` | `"True"` true, `"False"` false |
| String, Guid | `string` | the text |
| single-choice enumeration | `string` | the text |
| any List, multiple-choice enumeration | the list type | split on `"; "`, each item as above; empty text is an empty list |

- An error with `isAvailable` false is `{"status": "notAvailable"}`, with true
  `{"status": "notEvaluated"}`.
- The text `"<Undefined>"` is `{"status": "userUndefined"}`, whatever the declared type.
- A text that does not parse as its declared type (a boolean that is neither word, a number
  that is not one) becomes a `string` cell with the text and `"unparsed": true`. It is never
  dropped and never guessed.

The definitions come from one `GetAllProperties` call per fetch that has gaps, and the same
list is what keeps an unknown id out of the Tapir call.

### 3. A cell the server could not read is not "not available"

`Cell` gets a fourth status, `notRead`. No operator matches it: not `available`, not
`not_available`, not `has_no_value`. `find_elements` counts such cells in a new result field,
`not_read`, with the reason and the property names, next to `property_reads`.
`get_element_data` returns the reason in place of the value. A rule that reads such a cell
skips with the reason, as it does when a command is missing.

### 4. Who asks for the fallback

| Caller | Fallback | Why |
|---|---|---|
| `get_element_data` | yes | a read |
| `find_elements` | yes | a read; `property_reads` counts the Tapir cells too |
| `build_snapshot` (rules, audit) | yes | a read |
| `lock_reasons` | yes | the lock of a 2D element |
| `ac.props` in scripts | yes | a read (`ac.details` reads no property values) |
| `plan_property_writes`, `read_back_properties` | no | the official API cannot write what it cannot read, and the typed cell must be Archicad's own |
| `_shared_id_warnings` in `create_elements` | no | model elements and a supported property |

`set_element_data` on a pair in a gap refuses with the `notRead` reason, where today it
reports the property as not available.

### 5. The version gate

`useSIUnits` is a parameter, not a command, so no registration check can see it. The gate is
the reported Tapir version, 2.0.0 or newer, the way `modify_elements` gates the `fields`
filter on 1.5.9. `includeAvailability` is older (it is in the 1.8.0 definitions) and needs no
gate of its own. Below 2.0.0, and without Tapir, there is no fallback and the gaps stay
`notRead`, with a reason that names Tapir 2.0.0 as what would read them.

## What stays as it is

- The official read is tried first for every pair, and its typed cells are used unchanged.
- The element ceiling, the chunking and the classification pre-check in `find_elements`.
- Writes. Nothing here sends a write command.
- `docs/known-issues.md` keeps its advice to save first. The fallback adds reads, and no read
  class is known to be safe.

## To settle live before it ships

Each on MCP-Test, and each changes the code if it comes out otherwise.

1. A locked label and a locked polyline: `find_elements` with `General_Locked equal true`
   finds them, and `move_elements` names the lock.
2. A non-empty list through Tapir: real, integer and text, and a multiple-choice
   enumeration. MCP-Test had only empty lists and `"51; 59"`.
3. `"True"`, `"False"` and `"<Undefined>"` on an Archicad in another language. If they are
   translated, the boolean and the undefined mapping need the definition's own words, or the
   fallback reports them `unparsed`. Until this is run the mapping holds for English only.
4. A fetch of 500 elements with gaps on Tapir: that it answers, and how long it takes next to
   the official read.
5. Sub-elements (a stair's treads, a curtain wall's panels), which Tapir's description says
   it reads and the official read does not.
6. Tapir's source for the command, to see whether it goes through the same value composition
   as the official one. That says whether the fallback widens the crash surface or sits
   beside it.

## Not in this design

- Asking Tapir upstream for a `status` and a `type` on each value. It would remove the text
  parsing in section 2, and it is worth raising, but the fallback does not wait for it.
- Reading IFC properties, which go through other commands.
- Lifting the element ceiling.

## Amended after the whole-branch review, 11.11.2026

- **Rules.** This replaces "a rule that reads such a cell skips". An element of a type the rule
  applies to on which a value it needs could not be read is left out. The rule checks the rest:
  if one fails, the rule fails, and its message ends with how many elements could not be read
  and were not checked; if none fails and some were left out, the rule skips, naming the
  property, the count and the reason. All four rules that read properties or the layer use one
  shared helper, `ifc-property-required` included.
- **Reasons.** A refused cell carries its cause beside the reason. The causes are "the official
  API does not support this property definition" and "the official API reads no property of
  this element (2D and annotation elements, and sub-elements such as stair treads and curtain
  wall panels, are outside it)". With the gate closed the reason adds "; Tapir 2.0.0 or newer
  reads it" (or "reads them"). With the gate open, a cell Tapir gave no value for adds "; Tapir
  did not answer it either". A write on such a cell is skipped with the cause and "; property
  writes go through the official API, so it cannot be written".
- **Sub-elements.** The official API also refuses sub-elements (stair treads, curtain wall
  panels) whole, as it does 2D elements, and Tapir reads them.
- **General Parameters / Layer** is an integer, the layer's index, not its name.
