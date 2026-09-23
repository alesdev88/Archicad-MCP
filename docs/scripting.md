# Scripting: run_script and apply_changeset

Bulk tasks (numbering 400 doors, filling a property from another one) take two
calls instead of hundreds. A short Python script runs next to Archicad, reads
what it needs locally, and plans its writes. Only what the script returns, plus
a preview of the planned changes, goes back to the conversation.

## Switching it on

Off by default. Turn it on with `--enable-scripts`, the environment variable
`ARCHICAD_MCP_SCRIPTS=1`, or **Enable scripts** in the Claude Desktop extension
settings. Mode must be `full`.

With `--transport http` the server refuses to start unless you also pass
`--allow-scripts-over-http`.

## Trust model

There is no sandbox. A script is ordinary Python running as your user on the
machine that runs Archicad, and it can do anything you can. The switch is the
boundary: enable scripts only for clients you trust, and do not expose them
over http to anything you would not let run code on this computer.

What the design does guarantee is that nothing written through `ac` reaches
the model before you have seen it: `run_script` only plans, and
`apply_changeset` needs `confirm=true`.

## The flow

1. `run_script(code, port)` runs the script in a separate process, stopped
   after `timeout_s` (default 120, at most 600). It returns `result`, captured
   `stdout` and, if the script planned writes, a `changeset`: its id, counts,
   20 sample changes (current and new value) and skipped changes with reasons.
2. `apply_changeset(changeset_id, confirm=true)` writes exactly what was
   previewed, in order, then reads every written property back. It returns
   `applied`, `failed` (guid, property, Archicad's code and message),
   `mismatched` readbacks, and `commands` (one outcome per recorded API
   command). If a property batch or a command fails, apply stops there:
   the report carries a `stopped` field naming where it stopped, and nothing
   queued after that point runs. What already applied stays applied.

A changeset applies once, expires after 30 minutes, and is refused if a
different project is now open on its port. A server restart loses it; rerun
the script.

## The `ac` object

| Call | Returns |
|---|---|
| `ac.find(groups, selection_only=False)` | GUIDs, with the same groups as `find_elements` |
| `ac.props(guids, ["Group/Name", ...])` | `{guid: {name: value}}` |
| `ac.details(guids)` | `{guid: details}` from Tapir `GetDetailsOfElements` (`floorIndex`, `id`, `layerIndex`, and per type data such as a wall's `begCoordinate`) |
| `ac.cmd(name, params)` | a read's response; a write is validated and recorded, returning `{"recorded": n}` |
| `ac.set_props([(guid, "Group/Name", value), ...])` | `{"planned": n, "skipped": m}` |
| `ac.port` | the Archicad port |

Refusals from Archicad raise `ac.ArchicadError` (`.code`, `.message`).

`set_props` checks each value against the property's type before planning it.
An Integer property cannot take `"001"`; that change is skipped with a reason
naming the fix (a whole number, or change the property to String in Property
Manager). Enum properties are skipped too; set them with `ac.cmd` and the
enum's id.

Property and detail reads keep the element ceiling
(`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS`, default 5000), because wide reads have
crashed Archicad. Read in scoped chunks: by element type, story or
classification.

## Example: number doors per storey

This assumes `Pozicija` is a String property: a zero-padded number like `"001"`
needs String, since leading zeros do not fit an Integer.

```python
doors = ac.find([{"element_types": ["Door"]}])
floors = {g: d["floorIndex"] for g, d in ac.details(doors).items()}
ordered = sorted(doors, key=lambda g: floors[g])
ac.set_props([(g, "ELEA - Vrata/Pozicija", f"{i:03d}")  # a String property: an Integer one cannot hold "001"
              for i, g in enumerate(ordered, start=1)])
result = {"doors": len(doors),
          "per_storey": {f: sum(1 for g in doors if floors[g] == f)
                         for f in sorted(set(floors.values()))}}
```

`run_script` returns the counts and a preview; `apply_changeset` with the
returned id and `confirm=true` writes them. Against an Integer property, the
zero-padded values (`"001"` to `"099"`) are listed under `skipped` with the
reason, while `"100"` and up are planned as whole numbers, so check the
preview's skipped count before applying; send whole numbers, or change the
property to String.
