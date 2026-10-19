---
title: Reading and writing element data
---

# Reading and writing element data

`get_element_data` reads, and `set_element_data` writes, five kinds of element
data through one address syntax. A value read with an address can be written
back with the same address, and one `set_element_data` call can mix kinds.

## Addresses

| Address | What it reads and writes | Value |
|---|---|---|
| `Group/Name`, a built-in name, a property GUID | a property, as `search_definitions` gives the address | the property's value |
| `classification:<System>` | the element's item in that classification system | the item ID; a write also takes the item GUID, and null unclassifies |
| `gdl:<name>` | a GDL parameter of an object, lamp, door, window or skylight (reads reach every library part element) | see Units |
| `marker:<name>` | a parameter of a door's or window's marker (the stamp on the floor plan) | see Units; no arrays |
| `ifc:<Pset>.<Name>` | a local IFC property | text as Archicad shows it; a write takes text, a number or true/false |

- `<System>` is the system's name, or `name version` when two systems share a name.
- GDL and marker names are matched exactly, then case-insensitively.
- `ifc:` splits at the first dot: `ifc:Pset_DoorCommon.FireRating`.
- `gdl:*`, `marker:*`, `ifc:*` and `classification:*` read every value (GDL
  parameters flagged Hidden are left out), with the display names in `labels`.
  A door is several hundred values, so a wildcard call takes at most 20
  elements: read one element with it, then name what you need for the rest.
- A prefix wins over a property name: a user property group literally named
  `gdl:x` has to be addressed by its GUID.

## Units

GDL and marker values come back the way Archicad stores them and are written
the same way:

- Length in metres, Angle in radians, other numbers as numbers.
- Integer and pen numbers as whole numbers. Line type, surface (Material), fill,
  building material and profile parameters read as attribute indexes; a write
  takes the index or the attribute's name.
- Boolean true or false; a light switch "On" or "Off".
- Text up to 511 characters (255 on a marker); Archicad would cut the rest.
- Arrays as a list, or a list of rows for two dimensions. A write sends the
  whole array; read it first to change one item.

Tapir takes a single GDL angle in degrees while it stores and reads radians;
the tool converts, so radians are right everywhere.

## Reading

```
get_element_data(guids=[...], properties=["OFFICE/Status", "gdl:A",
                 "classification:ARCHICAD Classification", "marker:*"])
```

Each element carries `type`, `layer`, `properties` keyed by the address you
gave, `labels` for wildcard reads, and `unreadable` naming a kind that could not
be read for it (a wall has no GDL parameters, a door without a marker, a Tapir
too old for the command). `include_classifications` still adds every system's
item GUID.

## Writing

```
set_element_data(changes=[
  {"guid": "...", "property": "classification:ARCHICAD Classification", "value": "Door"},
  {"guid": "...", "property": "gdl:A", "value": 1.0},
  {"guid": "...", "property": "marker:iNumber", "value": "001"},
  {"guid": "...", "property": "ifc:Pset_DoorCommon.FireRating", "value": "EI30",
   "ifc_type": "IfcLabel"}])
```

The dry run (the default) reads every value it would change and shows
`current` and `new` per change, with a `warning` where Archicad may keep or
replace the value (a parameter locked by its script, a value outside the
parameter's list). Changes that cannot be sent are listed under `skipped` with
the reason: a value that does not fit, an unknown parameter, a GDL write on a
wall, a door without a marker, or an element Archicad would refuse (in another
view's database, on a hidden or locked layer, inside a hotlinked module, locked
with Edit > Locking, not reserved in Teamwork).

A commit sends classifications first, because a property's availability follows
the classification: a property that only exists under the new classification is
skipped with a note to send it in a second call. Then properties, GDL and marker
parameters (one entry per element), and IFC properties. Every change is read
back:

- `applied`: changes that now read as sent.
- `not_applied`: Archicad answered success but kept or replaced the value. A
  GDL Parameter Script can do this; the sample shows what was sent and read.
- `failed`: Archicad's refusals, grouped by code and message.
- `not_sent` and `stopped`: a batch was refused outright (Archicad answers 4001
  while someone works in it); what landed before it stays counted.
- `unconfirmed`: the read after the write failed. Run a dry run before retrying.

## Requirements and limits

- Properties and classifications work without Tapir (classifications use Tapir
  when present, which also reaches curtain wall, stair and railing parts).
- GDL parameters need Tapir 1.5.7, marker parameters Tapir 1.7.1.
- A and B of an object or lamp need Tapir 1.6.0: older versions write them in
  the wrong unit when the object's size is not fixed.
- IFC properties: Tapir writes them only in Archicad 25 to 27 (Tapir 1.7.0 or
  newer). From Archicad 28 every `ifc:` change is skipped with that reason;
  map the value from an Archicad property in the IFC translator instead.
- Doors and windows created through the API (`create_elements`) have no
  marker, so `marker:` reads and writes need doors placed in Archicad.
