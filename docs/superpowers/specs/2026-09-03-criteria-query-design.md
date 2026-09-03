# Criteria query and definition search (v0.4.0)

Date: 03.09.2026. Status: implemented on `feat/criteria-query`, live canary
against the Oprema-objekti test model on port 19724.

## Why

The comparison against the Archicad 30 RC1 built-in MCP server
(`graphisoft-public-mcp-030926.md` in the second brain) measured two gaps:
`query_elements` was five AND-combined filters against Graphisoft's
`criteria_evaluator`, and there was no way to discover a property without
already knowing its `Group/Name`. This closes both. Everything else in that
note (Teamwork reserve/release, navigator, keynotes, pagination) is out of
scope here.

## Sequencing

v0.3.0 (GDL library-part tools) was tagged at `0a972bf` before this started;
its diff touched `server.py` only to register the GDL tools and never touched
`core/query.py`, the rules engine or the extractor. This branch is off the
`v0.3.0` tag.

## Probe results, before any design

Method note, repeated because it paid off again: cheap live probes, never
inference from headers. Offline sweep of the 311-command catalog first, then
definition-level live calls (no property value reads) on AC 29 build 5101,
Tapir 1.5.9, ports 19724 and 19725.

| Probe | Result | Consequence |
|---|---|---|
| Any command filtering elements by property value? | None. Tapir `FilterElements` has 13 visibility/editability flags. Official `GetElementsByClassification` matches one exact item (0 hits for a top-level item that has classified descendants). | Filtering is client-side after `GetPropertyValuesOfElements`. The design is about reading less. |
| Tapir `GetAllProperties` | 1619 built-ins + all custom definitions, with group, name, GUID, value/measure/collection type, `propertyIsEditable`, expression flag, enum values. One call. | Discovery source. `propertyIsEditable` is Graphisoft's `value_can_be_editable`. |
| Official `GetAllPropertyNames` + `GetPropertyIds` | 641 built-ins by API name; all 641 resolve and their GUIDs are in Tapir's list. ~1000 Tapir built-ins have no API name. | Built-in address is the API name when one exists, else the GUID. `resolve_property_ids` now accepts GUIDs. |
| `Group/Name` uniqueness | 11 duplicate pairs among built-ins; custom pairs unique (228/228 matched `localizedName`). | Never hand out `Group/Name` for a built-in. |
| `GetPropertyDefinitionAvailability` | Custom definitions list the classification items they apply to, expanded (parents and children). Built-ins list nothing. Prediction vs `GetAllPropertyIdsOfElements` on a wall: 0/228 disagreements. | Availability pre-check for custom properties without a value read. |
| `GetAllPropertyIdsOfElements` | ~30 KB per element. | Too heavy as a general pre-check; used only to validate the cheap one. |
| `GetAllClassificationsInSystem` | 847 items, depth 6, in one call. | Branch tests are client-side over the tree. |
| `GetClassificationsOfElements` | Item under `classificationItemId`, absent when unclassified. | **Bug found**: the extractor read `classificationId`, so every element was unclassified. Fixed. |
| `GetAttributesByType` | 10 of the 12 schema types answer; MEPSystem and OperationProfile refuse with 4002. | `search_definitions` covers the ten. |
| Property cell statuses (bundled schema) | normal / userUndefined / notAvailable, plus an error cell. | The four senses of empty map directly. |

## Tools

`find_elements(groups, selection_only=False, port=None)` and
`search_definitions(query, kind="any", alternatives=None, editable_only=False,
limit=25, port=None)`. Full shapes, operators, units and the read order are in
`docs/query.md`; that document is the contract.

One discovery tool, not two: the only parameter that would have been a union
(a property-group filter vs an attribute-type filter) disappears when the
searchable text for an attribute is `<Type>/<name>` and for a property
`<Group>/<Name>`, so "layer wall" and "ELEA vrata" both work through `query`.

`query_elements` is removed, not deprecated. `list_attributes` stays as the
exact listing of one type.

## Rules engine

The rules engine never called `query_elements`; its query path is
`extract.build_snapshot` plus `element_type_scope`. The migration therefore is
the shared evaluator: `archicad_mcp/criteria.py` has no transport imports, the
tool and the rules both use it, and `applies_to.where` lets a rule scope with
the same comparisons `find_elements` takes. Branch operators are refused in
rules at load time because a snapshot carries item GUIDs, not the tree.

## Blast radius

Preserved: dry-run by default on every write, `confirm=true` on the destructive
ones, the element ceiling. Added: server-side type narrowing, cheap
comparisons first in `and` groups, the availability pre-check, per-availability
request splitting, and `property_reads` in every result so the cost is
visible. Stated honestly in `docs/known-issues.md`: bounding, not a fix.

## Tests

Offline: `tests/test_criteria.py` (every operator), `tests/test_query.py` (what
is read, in which order, what is skipped), `tests/test_definitions.py`,
`tests/rules/test_applies_to_where.py`, ported coverage and tier-2 tests, and
fixtures recorded from the live shapes above. Live: four canaries appended to
`tests/test_live.py`, run with `ARCHICAD_MCP_LIVE_PORT=19724 uv run pytest -m live`.

## Left out, deliberately

`has_default_value` / `has_custom_value` (no API signal), opaque element sets,
pagination, a required alternatives list.
