# Tools

What each tool does, what it checks before it writes, and what it needs. The
[README](../README.md#what-you-can-ask) has the one-table overview.

Every tool that changes elements, their data, attributes or definitions is a dry
run by default and shows current and new values; pass `dry_run=false` to commit.
Delete, move, Teamwork reservations and the gateway's writes need `confirm=true`
instead. A few tools act at once: the view and selection tools, the issue and
BCF tools, `publish`, `learn_library_part` (it places samples and deletes them
in the same call), `record_library_part` (it writes only the server's own
learned-parts file) and `run_script` (it plans its writes; `apply_changeset`
sends them). A tool that needs a newer Tapir than the one installed refuses
before sending anything and names both versions.

Reads and writes are separate tools, and every tool declares whether it is
read-only or destructive, so a client can run reads without asking and prompt
for writes. The gateway splits the command catalog the same way, by command
name, with anything unrecognised treated as a write.

## QA

`list_instances`, `get_model_summary`, `list_rules`, `run_rule`,
`audit_delivery_readiness`, `verify_ifc_export_readiness`, `highlight_failures`,
`create_issues_from_failures`, `save_project`. These are the tools of `verdicts`
mode too.

`save_project` is File > Save behind `confirm=true`, for the moment before a
property read that could crash Archicad. A refused save is explained: a project
made in an older Archicad needs File > Save As once, as does one never saved, and
the tool does no Save As itself. `file_updated` says whether the file on disk
changed. On a Teamwork project it saves your local copy and sends nothing.

`highlight_failures` zooms to the failures as well as highlighting them; a rule
with no failures removes the highlight. Rules are described in
[Writing rules](rules.md).

## Finding and reading

`find_elements` is a criteria query in the shape of Archicad's Find & Select:
groups of comparisons, AND or OR within a group, OR between groups, an
element-type filter per group, 22 operators including string matching,
classification branch tests and the four senses of "empty". Story comparisons
need no element read on Tapir 1.7.3, so they have no element ceiling there.
`search_definitions` is the step before it: a fuzzy, accent-insensitive search
over property and attribute definitions that returns the exact address the
other tools accept and whether the value can be written. Both are documented in
[Finding elements](query.md).

`get_element_data` reads properties, classifications (`classification:<System>`),
GDL parameters (`gdl:<name>`), door and window marker parameters
(`marker:<name>`) and local IFC properties (`ifc:<Pset>.<Name>`), in one call.
See [Reading and writing element data](element-data.md).

`find_collisions` is a clash check between two groups of elements, or within one.
It returns the colliding pairs with both elements' type and ID, counts by type
pair, and every element involved, ready for `show_elements`. A volume tolerance
ignores small overlaps; a surface tolerance also reports touching faces.
Archicad leaves out what its own junctions clean up, such as crossing walls, so
an empty answer is not proof that nothing overlaps. Clashes can also be a QA
rule, `no-collisions`. Needs Tapir 1.5.7.

`get_project_info` and `list_attributes` read the project's stories, location,
info fields and attributes.

## Changing

`set_element_data` writes the same addresses `get_element_data` reads, mixed in
one call. A commit sends classifications first and reads everything back, so a
GDL value a Parameter Script replaced shows up under `not_applied`. Lengths are
in metres and angles in radians throughout; the tool converts for Tapir before
1.7.3, which took GDL angles in degrees. On Tapir 1.7.3,
`run_parameter_script=true` runs the Parameter Script of the elements written,
so parameters that depend on them update. IFC writes work only in Archicad 27
and older, where Tapir supports them.

`modify_elements` changes elements that already exist: story, layer, draw order
and (Tapir 1.7.3) Element ID for any element, and for walls, beams, slabs,
columns, doors, windows, morphs, multi-plane roofs, meshes, objects, lamps,
texts and labels the fields of Tapir's own Modify command (height, thickness,
sill height, sizes, attributes by name). A story change moves the element with
the story, as Archicad does; `keep_elevation` keeps it in place. Elements
Archicad would refuse (another view's, a hidden or locked layer, a hotlinked
module, locked, not reserved) are named and skipped, never unlocked. A commit
reads the elements back, so a field Archicad answered success for but did not
change shows up under `not_applied`. Needs Tapir 1.5.9.

On a Tapir newer than 1.8.0 it also changes stairs and railings, and the name and
reference ID of sections, elevations and detail and worksheet markers, and a
marker's clip polygon. `id` stays the Element ID everywhere, so the reference ID in
the marker is `reference_id`. Archicad solves a stair again after a change and may
adjust the step values, which show up under `not_applied`. MEP elements stay in the
gateway (`ModifyMEPElements`): Tapir names them `Unknown`, so the tool cannot choose a
command from the type.

`apply_favorites` makes elements match an office favourite: "make these 40 doors
Internal Door". Without a favourite it lists the ones that fit. The dry run
warns what a favourite takes over besides materials and pens, because Tapir's
"never geometry" is not what Archicad does: live, a wall took the favourite's
height, top link, reference line and layer, and doors and windows its library
part, size and sill. Each element keeps its own Element ID by default (`keep`),
since a favourite's ID is a template value; `keep` also takes the layer. On
Tapir 1.7.3 Tapir keeps them itself, `keep` also takes `width`, `height`,
`sillHeight`, `referenceLineLocation`, `topOffset` and `relativeTopStory` (keep
the last three together), and the dry run names the favourite's library part,
marker and folder. On a Tapir newer than 1.8.0 and Archicad 29 it also names the
favourite's layer and Element ID, and warns only about elements that would change.
On Tapir 1.8.0 a door or window keeps its own library part,
Element ID and layer: live, it took only the favourite's size and the parameters
both parts have, so changing the part takes `ChangeLibraryPartOfElements`
through `execute_write_api_command`. A commit reports every field that changed,
per element. `to_defaults=true` sets a tool's defaults instead. Needs Tapir 1.5.7.

`move_elements` and `delete_elements` need `confirm=true`. Archicad changes only
elements in the active window's database and answers success for the rest, so
both leave out the elements it would refuse and say why; delete reads back
afterwards what actually went.

`edit_attributes` creates, changes and deletes attributes (layers, layer
combinations, lines, fills, zone categories, pen tables, profiles, building
materials, composites, surfaces) and sets their property values. Tapir's
attribute commands create or overwrite, and an overwrite quietly drops what it
is not sent, so a `create` never replaces an existing attribute, a `modify` keeps
every field it does not change, and values Tapir would silently replace with a
default are refused. A layer is deleted only when no element is on it. Attribute
writes cannot be undone in Archicad. Needs Tapir 1.5.4.

On a Tapir newer than 1.8.0 and Archicad 27, `{"replace": "Fill", "attribute": ...,
"with": ...}` deletes a fill, line, surface or building material and rewrites
everything that used it to another one of the same type, like Delete and Replace in
the Attribute Manager. One call changes the whole project, in one undo step per type,
so read the dry run: it lists the elements, tool defaults, favourites, other attributes
and override rules that use it. Elements on hidden or locked layers change too, as
Tapir shows and unlocks those layers and puts them back. Hotlinked elements cannot be
changed, indices typed into GDL scripts are not found, and if an element cannot be
changed the attribute stays and the change reports under `failed`.

`edit_property_definitions`, `edit_classifications` and `import_definitions` edit
property definitions and classification systems in place, instead of the XML
round trip through Property Manager or Classification Manager. A property can be
renamed, moved, given a new default or expressions, made available for a
classification branch, and have its enum options renamed, removed, added or
reordered. Everything keeps its GUID, so values on elements survive, except
where you remove an enum option (those show `<Undefined>`) or availability.
Needs Tapir 1.6.0.

## Creating

`create_elements` places any of the 51 element types Tapir can create: walls,
beams, columns, slabs, roofs, shells, morphs, meshes, stairs, railings (also along
a 3D polyline), zones, curtain walls, grid elements, objects, lamps, doors and
windows in walls, skylights in roofs and shells, openings (also from the Opening
tool's settings, Archicad 29), analytical loads, supports and links (Archicad 26),
MEP routes, elements, flexible segments and take-offs (Archicad 28), electrical
panels, devices and circuits (Archicad 30), sections, elevations (Tapir 2.0), interior elevations, detail
and worksheet markers, hotlink instances, 2D lines to texts and labels, keynote
labels, dimensions and drawings on a layout. Shells, skylights, polyline railings,
flexible segments, take-offs and the two markers need a Tapir newer than 1.8.0. Items use Tapir's own
fields; `items=[]` returns a type's schema with notes on what it leaves out. The
dry run validates every item, checks that a named favourite or library part
exists (with the closest names when it does not), and checks hosts for
existence, type and editability, so an item that would fail is skipped with its
reason. It warns about an object without `dimensions` (it would take the tool's
current size), about `offset` and `useFixedAngle` (Archicad discards them), and
about a door or window without `sillHeight`. On Tapir 1.7.3 most types also take
the Element ID as `id`. Model elements are placed from the floor plan or the 3D
window; 2D elements go into the active drawing window, or, on a Tapir newer than
1.8.0, into the database given as `database` (a `{"guid": ...}`, for lines, arcs,
circles, polylines, splines, hatches, hotspots, texts and labels). With `database`,
a label's parent is not checked, because that check knows only the active window's
elements.

`create_swept_beam`, `create_road_marking` and `create_road_arrow` place GDL objects
of this project along a path or an existing element: a beam swept along a 3D path
([Swept Beam](swept-beam.md)), road markings draped over slabs and meshes
([Road marking](road-marking.md)), and road arrows at stations along a lane
([Road arrow](road-arrow.md)).

`learn_library_part`, `record_library_part` and `list_library_part_records`
teach the server a library part once, so a sketch can become a placement.
Archicad's API does not say where a part's insertion point sits on its
footprint, which end of a bed is its head, or what `reflected` and `oSide` do to
a door's hinge and swing. `learn_library_part` places a few samples just past
the model's right edge, measures their footprints, returns one floor plan image
of them and deletes them again. The agent looks at the image and records what it
shows with `record_library_part`. Then `create_elements` and `modify_elements`
take `placement` instead of coordinates, angle, `reflected` and sizes: an
object's footprint centre, size and head direction, or a door's or window's two
points and which leaves open. The records are one JSON file on this machine,
shared by every project. Learning needs Tapir 1.7.2. On Tapir 1.8.0 and older a door
or window learned leaves the Door or Window tool holding the part, with parameters
that cannot be read. On a newer Tapir, learning samples an object or lamp at the part's
own size and 1.5 times it, and the record keeps that own size: `create_elements` then
takes an object's `placement` without `size`, and fills a door's or window's width and
height when the item leaves them out. `modify_elements` still needs `size`, since an
element already placed has one. The part's own values are as stored in it, so one that
clamps its size may draw otherwise; the existing warning catches that.

`preview_library_parts` draws the 2D symbols of up to 8 parts in one image with nothing
placed, so an agent can compare candidates before learning one. It gives each part's default
size and extent as JSON. It shows the default parameters, and only lines, arcs and polygons,
so it does not replace the look after placing. Needs a Tapir newer than 1.8.0.

The tool descriptions carry the rules for reading a sketch, so the agent follows
them without being told: learn a part before placing it; choose the part from
what the sketch draws (the lines dividing a window give its sashes, the arcs
which one opens); treat width as a check and ask when count and width disagree;
the head of a part is the end against the wall; and after placing, look with
`capture_window` and correct before reporting done.

## Looking

`show_elements` is how an agent points at something. It finds the view that
holds the elements (the floor plan with their story, or a section, elevation,
detail, worksheet or layout), opens it, selects, highlights and zooms. Archicad
answers success for selections and zooms it did not make, so the selection is
read back and the story or window checked after the switch. `guids=[]` removes
the highlight and the selection. Needs Tapir 1.3.1; the story switch needs 1.7.0.
On a Tapir newer than 1.8.0, `in_3d=true` opens the 3D window on just the floor
plan's elements among them, as Show Selection in 3D does, and reports the rest where
they are. They always become the selection, 2D elements show nothing there, and
`guids=[]` shows everything.

`capture_window` is how an agent looks: it saves what the current window shows
(floor plan, section, 3D, layout) as a PNG and returns it as an image, so a
placement can be checked by eye. Call `show_elements` first to bring the
elements into view. Needs Tapir 1.7.2.

`get_selection`, `set_selection` and `clear_selection` read and set the
selection.

## Issues, output and Teamwork

`list_issues`, `create_issue`, `add_issue_comment`, `attach_elements_to_issue`,
`export_issues_bcf` and `import_issues_bcf` work with Archicad's Issue Manager
and BCF files.

`export_ifc` saves the project as `.ifc` or `.ifczip` with a translator chosen by
name and one of Archicad's five element scopes. It never replaces an existing
file without `overwrite=true`, checks the file is there afterwards, and never
opens or merges IFC. Needs Tapir 1.6.0. `publish` runs a Publisher set; on a Tapir
newer than 1.8.0 an unknown name is refused with the project's sets.

`reserve_elements` and `release_elements` are confirm-gated. A dry run reports
unknown GUIDs, elements already in your workspace and, on a Tapir newer than
1.8.0, who holds the rest; on older Tapir that is only learned by attempting. A
reservation reports what was reserved, what others hold (with their names), any
GUIDs that are not elements, and what Archicad pulled in on the side: reserving
one door also reserved its wall and the wall's other door. Both also take
`object_sets`: an attribute type, `Properties`,
`Classifications` or any Archicad set name (`Favorites`, `ProjectInfo`, ...),
for which the dry run always says who holds them. In a Teamwork project the
definition tools and `edit_attributes` refuse an edit whose set is not reserved
and name the call to reserve it. Needs Tapir 1.7.1 for the sets.

## Schedules

`read_schedule_scheme`, `edit_schedule_scheme` and `validate_schedule_scheme`
work through Scheme Settings' XML export and import, because Archicad has no
schedule API at all. See [Schedules](schedules.md).

## Library parts from meshes

`list_gdl_sources`, `inspect_gdl_source`, `build_gdl_object` and
`deploy_gdl_object` turn mesh models (OBJ, 3DS) into placeable library parts
with finish variants, without the GDL editor. Deploying places the object,
renders it and returns the image, because Archicad silently drops defective 3D
bodies that every offline validator passes. On a Tapir newer than 1.8.0 it also
says whether the part's script ran and what it made. Needs the GDL workspace folder
set and added to Archicad's Library Manager once, which `link_workspace=true` can do
there. `replace_embedded=true` replaces a part already in the embedded library. See
[GDL pipeline](gdl-pipeline.md).

## Gateway and scripting

`list_api_commands`, `describe_api_command`, `execute_read_api_command` and
`execute_write_api_command` reach the whole official and Tapir command surface:
863 commands with the Tapir 2.0.0 definitions (73 official, 790 Tapir), 387
reads and 476 writes. The write half needs `confirm=true`, because it can reach
`DeleteElements` and `QuitArchicad`. The
[API dashboard](https://alesdev88.github.io/Archicad-MCP/api-dashboard.html)
shows which commands have a tool of their own.

`install_tapir` is the tool form of `archicad-mcp install-tapir`
([Install](install.md#tapir)).

`update_extension` moves the Claude Desktop bundle to the newest release. A dry
run compares the versions; `confirm=true` downloads the bundle, checks its sha256
and opens Desktop's install dialog
([Install](install.md#claude-desktop-extension)).

`run_script` and `apply_changeset` are off by default. `run_script` runs a short
Python script next to Archicad and returns what it returns; its writes are
planned into a changeset, which `apply_changeset` writes after `confirm=true`.
There is no sandbox. See [Scripting](scripting.md).
