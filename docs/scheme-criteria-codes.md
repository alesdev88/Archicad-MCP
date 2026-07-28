# Schedule criteria codes

Archicad encodes each schedule criterion as a numeric `Param_Type` and
`Relation_Index`. Neither is publicly documented, so this table is built
empirically and is the prerequisite for editing criteria rather than only
reading them.

## How to add an entry

1. Open a scratch project. Never use a client model.
2. Document > Schedules > Scheme Settings, pick a scheme, Export it as `before.xml`.
3. Change **exactly one** criterion in the dialog. One change per pair.
4. Export again as `after.xml`.
5. Run:

   ```bash
   uv run python scripts/diff_scheme_criteria.py before.xml after.xml
   ```

6. Add a row below with what you changed in the GUI and what the script reported.

## Confirmed codes

| Param_Type | Relation_Index | GUI meaning | Value field | Source |
|---|---|---|---|---|
| 88 | 1 | Element type is <class> | `ExtendedElem_ElemClassId` and `UniValue` carry the classification GUID | Observed in a real 29.0.0 door schedule |
| 232 | 12 | Property comparison on `ACPropertyGuid` | `UniValue` | Observed in a real 29.0.0 door schedule; the exact relation 12 means is not yet confirmed |

## Still unknown

Everything else. Priority order for the next exports: layer equals, element
type variants beyond Door, property is empty vs is not empty, property equals a
string, classification is, and the OR chaining that `AndNext` and the bracket
fields encode.
