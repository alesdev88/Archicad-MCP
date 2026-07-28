# Schedule criteria codes

Archicad encodes each schedule criterion as a numeric `Param_Type` and
`Relation_Index`. Neither is publicly documented, so this table is built
empirically and is the prerequisite for editing criteria rather than only
reading them.

## How to add an entry

1. Open a scratch project. Never use a client model.
2. Document > Schedules > Scheme Settings, pick a scheme, Export it as `before.xml`.
3. Change **exactly one field on exactly one criterion** in the dialog. One
   field change per pair.
4. Export again as `after.xml`.
5. Run:

   ```bash
   uv run python scripts/diff_scheme_criteria.py before.xml after.xml
   ```

6. Add a row below with what you changed in the GUI and what the script reported.

**Caveat: change one field, not the criteria set.** The script matches
criteria positionally: `before.criteria[0]` against `after.criteria[0]`,
`before.criteria[1]` against `after.criteria[1]`, and so on by index. Adding
or removing a criterion anywhere but the very end of the list shifts every
later index out of alignment, so each criterion after the change point gets
diffed against the wrong criterion in the other file. The script still
reports the honest signal, a `criterion_count` line, but alongside it you
get a cascade of misleading per-field differences that do not correspond to
anything you actually changed. Keep the number of criteria identical between
`before.xml` and `after.xml`, and change exactly one field on exactly one
criterion per pair.

## Confirmed codes

| Param_Type | Relation_Index | GUI meaning | Value field | Source |
|---|---|---|---|---|
| 88 | 1 | Element type is <class> | `ExtendedElem_ElemClassId` and `UniValue` carry the classification GUID | Observed in a real 29.0.0 door schedule; confirmed GUIDs below |
| 232 | 12 | Property comparison on `ACPropertyGuid` | `UniValue` | Observed in a real 29.0.0 door schedule; the exact relation 12 means is not yet confirmed |

### Confirmed classification GUIDs (Param_Type 88, Relation_Index 1)

Obtained by exporting a real Archicad 29.0.0 door schedule and a real window
schedule and diffing them with this script. Exactly two values changed
between the two files, `ExtendedElem_ElemClassId` and `UniValue`, and both
changed together to the same GUID:

| Element class | GUID |
|---|---|
| Door | `D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE` |
| Window | `93F161A0-4C4E-4DF7-A100-4FD0E8C4F1E1` |

These are measured, not guessed. They are Archicad's built-in element
classification GUIDs, so they are expected to be stable across projects, but
that has not been verified across more than the two files measured here.

## Still unknown

Everything else. Priority order for the next exports: layer equals, element
type variants beyond Door and Window, property is empty vs is not empty,
property equals a string, classification is, and the OR chaining that
`AndNext` and the bracket fields encode.
