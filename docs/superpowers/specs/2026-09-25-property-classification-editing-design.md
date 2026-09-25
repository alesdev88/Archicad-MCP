# Property and Classification Definition Editing: Design

**Date:** 2026-09-25
**Status:** Approved in brainstorming, not yet implemented.
**Repos:** https://github.com/alesdev88/Archicad-MCP.git (MCP tools) and the local
Tapir fork at `/Users/alesd/Developer/tapir-archicad-automation` (C++ commands).

## Purpose

Edit property definitions and classification systems from chat, in place, instead
of the Property Manager / Classification Manager round trip (export XML, edit,
import XML). Four kinds of edit are in scope, all confirmed as real, recurring
work:

1. Property availability (which classification items a property is available for).
2. Rename and restructure: properties, property groups, classification items and
   codes; moving a property to another group.
3. Enum options and defaults: rename, remove, reorder options; change the plain
   default value or the expressions.
4. Bulk setup from an office standard or a spreadsheet (mostly creation).

Success means each of these is one dry-run call and one commit call, with no
dialog, and element data survives every edit that is not an explicit removal.

## What the SDK offers (checked in the AC29 DevKit 29.3000 headers)

| Need | C++ API |
|---|---|
| Edit a property in place | `ACAPI_Property_ChangePropertyDefinition` (name, description, groupGuid, defaultValue incl. expressions, availability, possibleEnumValues) |
| Edit a property group | `ACAPI_Property_ChangePropertyGroup` |
| Edit a classification system / item | `ACAPI_Classification_ChangeClassificationSystem`, `ACAPI_Classification_ChangeClassificationItem` |
| Bulk import | `ACAPI_Property_Import` (append / replace / skip), `ACAPI_Classification_Import` (systems: merge / replace / skip; items: replace / skip) |

None of the edit or import functions is reachable from the official JSON API.

## What already exists

Tapir 1.5.9 (installed, fork build) has `CreatePropertyGroups`,
`DeletePropertyGroups`, `CreatePropertyDefinitions`, `DeletePropertyDefinitions`,
`CreateClassificationSystems`, `CreateClassificationItems`,
`DeleteClassificationSystems`, `DeleteClassificationItems`, and
`UpdatePropertyDefinitions` (1.5.4), which only replaces expressions and appends
enum values. `GetAllProperties` returns enum values with their GUIDs and the
expressions, but not description, group GUID, the plain default value or
availability. The official `GetPropertyDefinitionAvailability` covers
availability reads.

Nothing covers renames, availability edits, plain defaults, enum rename / remove
/ reorder, any classification edit, or import.

## Decisions taken

| Question | Decision |
|---|---|
| Where the C++ lives | New commands in the Tapir fork, PR-able upstream. Not a separate add-on (duplicate plumbing, second bundle to maintain). |
| XML import as the edit mechanism | Rejected as the primary path: import matches on name, so it cannot rename, and "replace" may regenerate GUIDs, orphaning element values and availability links. Import is kept for bulk setup only. |
| Identity | Every edit is read, patch the sent fields, `Change*`. GUIDs never change; unsent fields stay untouched. |
| Value type / collection type changes | Out of scope. Refused. |
| Classification item reparenting | Out of scope. The item struct has no parent field, so it would be delete plus recreate, which breaks element links. Refused. |
| Counting elements affected by a removal | Not done. It needs property value reads, the known crash path. Removals carry a warning instead. |
| Undo | Each command runs its whole batch in one `ACAPI_CallUndoableCommand`. |

## Part 1: Tapir commands

Branch `feature/property-classification-editing`, from upstream main. Every
command takes a batch and returns one `executionResult` per item; a failing item
does not abort the others.

### `UpdatePropertyDefinitions` (extended, backward compatible)

Per item: `propertyId` (required) plus any of:

- `name`, `description`
- `groupId`: move to another property group
- `defaultValue`: either `{basicDefaultValue}` (same shape as
  `CreatePropertyDefinitions`) or `{expressions: [...]}`. Switching between the
  two is allowed. The existing top-level `expressions` field keeps working.
- `availability`: `{add: [classificationItemId...], remove: [...]}` or
  `{set: [...]}`. `set` is exclusive with `add` / `remove`.
- `possibleEnumValues`: unchanged (append if not already present).
- `renameEnumValues`: `[{enumValueId, displayValue, nonLocalizedValue?}]`,
  keyed by the option GUID, so values on elements follow the rename.
- `removeEnumValues`: `[enumValueId]`. Refused if it would remove the option the
  default value points at, unless `defaultValue` is changed in the same item.
- `enumOrder`: `[enumValueId...]`, a full permutation of the current options
  after the add / rename / remove above. A partial or unknown list is refused.

Refused per item: built-in properties (`definitionType` not custom), and any
field that tries to change `valueType`, `collectionType` or `measureType`.
Archicad errors (`APIERR_NAMEALREADYUSED`, `APIERR_BADVALUE`,
`APIERR_NOACCESSRIGHT`, `APIERR_BADID`) pass through as the item's error.

### `UpdatePropertyGroups`

Per item: `propertyGroupId` plus `name` and / or `description`. Built-in groups
are refused.

### `UpdateClassificationSystems`

Per item: `classificationSystemId` plus any of `name`, `description`, `source`,
`editionVersion`, `editionDate` (`YYYY-MM-DD`).

### `UpdateClassificationItems`

Per item: `classificationItemId` plus any of `id` (the code, e.g. `21.10`),
`name`, `description`. No parent field exists, so reparenting cannot be asked
for.

### `ImportPropertiesXml` and `ImportClassificationsXml`

Input: `xml` (string) and `conflictPolicy`
(`append | replace | skip` for properties; `systemConflictPolicy`
`merge | replace | skip` and `itemConflictPolicy` `replace | skip` for
classifications). The import calls return nothing but an error code, so the
command reads all groups and definitions (or systems and items) before and after
and returns `created` and `removed` id lists alongside the `executionResult`.
Guid-preserving "replace" shows up as neither created nor removed.

### `GetAllProperties` (extended)

Adds `description`, `propertyGroupId`, `basicDefaultValue` (when not
expression-based) and `availability` (classification item ids) to each custom
property. The MCP needs these to show a real before / after.

### Examples and docs

One `Examples/*.py` per new command, as the fork's contribution rules require.
Command reference docs are generated from the registered descriptions. New
commands register under the version the fork is on at the time.

## Part 2: MCP tools (full mode)

Three tools, dry-run by default like `set_element_data`: the default call
returns the plan, `dry_run=false` commits it.

### `edit_property_definitions(changes, dry_run=True, port=None)`

Each change addresses a property by `"Group/Name"` (the address
`search_definitions` hands out for custom properties) or by GUID, and lists the
fields to change:

```json
{"property": "ELEA/Šifra opreme",
 "name": "Šifra",
 "group": "ELEA Oprema",
 "default": "/",
 "expressions": null,
 "description": "...",
 "availability": {"add": ["ELEA/40.10/*"], "remove": ["ELEA/40.20"]},
 "enum": {"rename": {"Kuhinja": "Kuhinjska oprema"},
          "remove": ["Staro"],
          "add": ["Pisarna"],
          "order": ["Kuhinjska oprema", "Pisarna"]}}
```

- Enum options are addressed by display text; the MCP resolves them to
  `enumValueId`. A text that matches two options is an error in the plan.
- Availability entries are `System/Code`. A trailing `/*` means the item and all
  its descendants, expanded against the live classification tree. `set` is
  accepted as an alternative to `add` / `remove`.
- `group` names an existing property group; a missing group is an error in the
  plan (creating groups stays with the existing Tapir command).
- The dry run returns, per property: the before / after diff of every touched
  field, the resolved ids, and warnings:
  - enum options removed: elements holding them lose that value;
  - availability removed: values on elements of those classifications become
    not applicable;
  - an unresolved property, group, classification address or enum text.
- A commit sends one `UpdatePropertyDefinitions` batch, re-reads the touched
  definitions and returns `applied` (with the after state) and `failed` (grouped
  by Archicad error, as `set_element_data` does).

### `edit_classifications(changes, dry_run=True, port=None)`

Each change addresses a system by name or an item by `System/Code`:

```json
{"item": "ELEA/40.10", "code": "40.11", "name": "Kuhinjska oprema"}
{"system": "ELEA", "version": "2026", "description": "..."}
```

The dry run flags a new code that collides with an existing sibling code and any
unresolved address. Commit sends `UpdateClassificationSystems` and / or
`UpdateClassificationItems` and re-reads.

### `import_definitions(kind, xml_path, conflict, dry_run=True, port=None)`

`kind` is `property` or `classification`. `xml_path` is a Property Manager or
Classification Manager XML, exported from Archicad or written by the client
from an office standard or a spreadsheet.

- Dry run: parses the XML (`core/definition_xml.py`), matches it by name
  against the live project, and reports what is new, what collides, and what
  the chosen `conflict` policy does to each collision.
- Commit: sends the import command and reports `created` / `removed` from its
  response.

### Plumbing

- `core/definition_edit.py`: address resolution, diff and warning building,
  sending, re-reading. Shared by the first two tools.
- `core/definition_xml.py`: XML parsing for the import dry run.
- No tool here reads property values, so none of them can hit the
  `GetPropertyValuesOfElements` crash.
- Capability gate: every tool first checks
  `conn.tapir_command_available("UpdateClassificationItems")`, the marker of the
  new build. If missing, it answers that the feature needs the fork build or the
  upstream Tapir release carrying these commands, and sends nothing.
- Teamwork: writes are attempted; `APIERR_NOACCESSRIGHT` is reported per item
  in plain words. Behaviour on a Teamwork project is verified live on CVP only
  with Aleš's explicit go-ahead.

## Testing

- Unit tests with fixtures recorded from the live build (`tests/fixtures/`):
  property address resolution, enum text to id (including ambiguous text),
  `System/Code/*` expansion, diff and warning builder, enum order validation,
  XML dry-run parsing, capability gate, Teamwork error mapping.
- Live canaries (`pytest -m live`, Oprema-objekti on port 19724 only), each
  restoring the state it changed, on the "MCP Test/Fire Rating" property and the
  "MCP Test" classification:
  1. rename a property and rename it back;
  2. set availability on a branch, restore it;
  3. add, rename, remove an enum option;
  4. change a classification item code and change it back;
  5. import a small XML with "replace" and check whether GUIDs survive.
- Two open questions are settled by those canaries and written into
  `docs/known-issues.md`: what an element shows after its enum option is
  removed, and whether import "replace" keeps GUIDs.

## Rollout

1. Prerequisite (Aleš): run `sudo xcodebuild -license` once. It currently blocks
   the Xcode build and `/usr/bin/git` (Homebrew git works in the meantime).
2. Tapir branch `feature/property-classification-editing` from upstream main.
   The fork currently has an uncommitted change in
   `ExtendedElementCommands.cpp` on `feature/section-dimension-chains`; ask
   before touching that branch.
3. Local integration branch merging the new branch with
   `feature/section-dimension-chains`, built with the recorded recipe, so the
   installed bundle keeps `CreateAssociativeDimensionChainsOnSection`.
4. Install: quit every Archicad instance (including the Teamwork twin), back up
   the 1.5.9 bundle to `~/Downloads/TapirAddOn_AC29_Mac.bundle.backup-1.5.9-<date>`,
   swap in the new bundle, relaunch the test model only.
5. MCP tools built against the live build; release as v0.7.0.
6. Teamwork check on CVP when Aleš says go.
7. Upstream Tapir PR for the new commands.

## Out of scope

- Changing a property's value, collection or measure type.
- Reparenting classification items.
- Creating or deleting property groups, properties, systems or items (Tapir
  already has these; reachable through the gateway).
- A Tapir export command (`GetAllProperties` plus the classification reads cover
  the read side).
- Counting elements affected by an enum or availability removal.
