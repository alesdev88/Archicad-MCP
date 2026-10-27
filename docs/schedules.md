# Schedules

Archicad exposes **no API for schedules at all**: not the JSON API, not Tapir,
and per Graphisoft not the C++ API either. What it does support is the XML
round trip built into Scheme Settings, and that is what these tools work
through:

1. In Archicad: Document > Schedules > Scheme Settings, select a scheme, **Export**.
2. Edit it: `read_schedule_scheme` to see what it does, `edit_schedule_scheme`
   to apply a YAML spec, `validate_schedule_scheme` to check its bindings
   against the open project.
3. In Archicad: Scheme Settings > **Import**.

A scheme spec looks like this:

```yaml
- id: door-schedule
  template: exports/door-scheme.xml
  name: "Door Schedule"
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating" }
      width: 40
```

A column binds three ways:

- `bind: { property: "<GUID>" }`, which needs no connection to Archicad, or a
  `"Group/Name"` string, which `edit_schedule_scheme` resolves by looking the
  name up in the open project. A spec that only uses GUIDs (plus `gdl_param`
  and `builtin` bindings) runs fully offline; one named property means Archicad
  must be open with the project that defines it.
- `bind: { gdl_param: "<parameter name>" }`, a library part parameter by name.
- `bind: { builtin: Quantity }` for the few named built-ins, or
  `bind: { builtin: { param_type: 0, param_index: -1561 } }` for any other
  built-in by its raw numbers.

The named table holds only `Quantity`: the codes behind it are undocumented and
are being mapped one confirmed example at a time. The raw-numbers form lets a
scheme be fully expressed even when a built-in has no name yet, which is not a
rare case: on a real 27-column door schedule, 2 columns need it.

A column can also carry `width: <number>`, which sets its cell width; a column
that already has that width is reported as a no-op. Only the portrait width is
guaranteed: the landscape width is updated when a column already has one, but
never created, since it is not confirmed that Archicad writes it for every
scheme.

## Limitations

- **Criteria are read and preserved but cannot yet be edited.** See
  [Schedule criteria codes](scheme-criteria-codes.md) for what is confirmed
  about the codes behind them, and what is still unknown.
- **Every edit needs two manual steps in Archicad**, Export before and Import
  after, because no API reaches schedules.
- **Whether re-importing an edited scheme updates it in place or creates a
  numbered duplicate is not yet confirmed.** Graphisoft's documentation says
  duplicate names are auto-numbered, but real exports carry stable scheme IDs,
  which suggests an in-place match may be possible. Test on a scratch project
  first.
- **`edit_schedule_scheme` refuses any file that would not survive a no-op save
  unchanged.** This protects the parts of the format the server does not model.
