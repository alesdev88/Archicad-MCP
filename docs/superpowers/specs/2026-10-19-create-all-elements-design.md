# create_elements: every element Tapir can create

Date: 2026-10-19. Asked for by Aleš: "add the ability to create everything via MCP that is possible to create." He skipped the design questions, so the decisions below are mine unless marked otherwise.

## Intent

`create_elements` covers 7 types (column, slab, zone, polyline, object, mesh, railing). Tapir has 33 commands that place an element, and the rest are reachable only through `execute_write_api_command`, with no dry run beyond schema validation and no host checks. Put every one of them behind `create_elements`, with a dry run that catches the mistakes an agent actually makes before anything is sent.

## Scope

In: every Tapir command whose answer is a list of created elements (`ElementIdsOrErrors`), plus the local fork's `CreateRailings`:

| Family | Types |
|---|---|
| Model | wall, beam, column, slab, roof, morph, mesh, stair, railing, zone, object, lamp |
| Hosted | door, window (in a wall), opening (in a wall, slab, roof, ...) |
| MEP (Archicad 28+) | mep_route, mep_element |
| Markers | section, interior_elevation |
| 2D | line, arc, circle, polyline, spline, hatch, hotspot, text, label, keynote_label |
| Dimensions | dimension, wall_thickness_dimension, section_dimension |
| Other | hotlink_instance, drawing (on a layout) |

Out: things that are not elements (attributes, layouts, views, details, worksheets, favourites, groups, property and classification definitions, design options, keynote items, hotlink nodes). Those are the `edit_attributes` / `edit_navigator` ideas in the Tapir triage.

Non-goal: re-modelling each type's fields. Items keep Tapir's own field names and units (metres, radians), so the tool follows Tapir's schema as it grows instead of a parallel one that drifts.

## Behaviour

`create_elements(element_type, items, dry_run=True)`. Signature unchanged.

- **Type names**: the singular names above, plus plurals and the Tapir command name (`walls`, `CreateWalls`). An unknown name lists the valid ones.
- **Schema on request**: `items=[]` returns the resolved item schema for the type, so an agent can learn the fields without a second tool.
- **Availability**: if the running Tapir lacks the command, refuse and name the Tapir version it arrived in.
- **Validation**: each item is checked against the bundled item schema. Problems are reported per item index with the field path. Any problem means nothing is sent, dry run or not, the same rule as the gateway.
- **Favourites**: an item that names a `favoriteName` is checked against `GetFavoritesByType` for its element type. An unknown name skips that item and suggests close matches.
- **Hosts**: door and window `ownerWallId`, opening `ownerElementId`, label `parentElementId`, wall thickness `wallId`, and dimension witness `elementId`s are checked with the write tools' editability check (`core/editability.py`): not found, hidden layer, not reserved, locked. Door and window hosts must be walls. An item with a bad host is skipped; the others go ahead.
- **Window**: the dry run reports the active window. Model types, hosted types and markers warn when it is not the floor plan; a drawing warns when it is not a layout and no `layoutDatabaseId` is given. Warn, not refuse, until live behaviour says otherwise (see Findings).
- **Dry run result**: command, how many items are ready, problems, skipped items with reasons, warnings, the active window, and the exact payload that a commit would send.
- **Commit result**: unchanged when every item is created (`{"dry_run": false, "created": n, "elements": [guids]}`). Otherwise also `failed` (Tapir's per-item errors grouped by code and message, with item indices), `skipped`, and `by_index` mapping item index to the new GUID.

## Findings

Live check on MCP-Test, 19.10.2026 (Archicad 29/5101, Tapir 1.5.10). Every created element was read back by type, then deleted; the model ended at its starting 366 elements.

- **33 of 34 types created.** Not checked: `hotlink_instance`, because MCP-Test has no hotlink module.
- **Tapir's per-item errors are the useful part of a commit.** First tries failed per item with clear messages, all reported by the tool with their indices: a morph needs exactly one of `size` or `body`, an MEP terminal needs `domain` (the schema says "required for all types except Equipment" but cannot enforce it), and a hatch whose last point repeated the first was refused ("Failed to create new Hatch"). The schema cannot catch these, which is why the commit result groups Tapir's errors instead of collapsing them.
- **Windows.** A wall created with the 3D window active landed on the floor plan. From a Section or a Layout window, Tapir answered "Failed to create new Wall" for the item and created nothing. So model types are accepted from the floor plan and the 3D window, and the dry run warns from anything else. 2D elements went into the active window's database: a text created from a layout was listed by `GetElementsByType` only with the layout active, a line created from a section only with the section active.
- **Checks worked live:** a misspelt favourite ("Generic Exterir Wall") was skipped with "closest: Generic Exterior Wall", and a door aimed at a slab was skipped as "is a Slab; a door goes in a Wall", while the valid items in the same calls were created.
- **Drawings** were created from the floor plan window when `layoutDatabaseId` was given, and landed on that layout.
- **Section dimensions** need section elements, and `GetSectionElements` returned none for a new section until its window had been opened once.
- **Cleanup notes:** a section marker counts as four elements in `GetAllElements` (the cut plane and three marker objects); deleting the cut plane removes all four. 2D elements are deleted only from their own window, like every element command.

## Decisions made during the build

- Window rule: warn, never refuse. The failure from a wrong window is a clean per-item error, so refusing would add nothing but a second place to keep the rule.
- Validation problems block the whole call, dry run or commit, the same rule as the gateway. If the bundled definitions disagree with a newer add-on, the fix is syncing the definitions.
- The commit result keeps its old shape when every item is created, so existing callers are unaffected.

## Builder's notes

- Element commands act on the active window's database (verified for delete and move on 28.09.2026). Whether create behaves the same is part of the live check.
- The bundled definitions on `main` are Tapir 1.5.9; the live add-on is 1.5.10. `CreateBeams`, `CreateDrawings`, `CreateLabels`, `CreateMEPRoutingElements` and `CreateTexts` changed shape in 1.6.0 or 1.7.0, so the live check uses fields valid in both.
