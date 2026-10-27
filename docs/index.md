---
title: Archicad MCP docs
---

# Archicad MCP

Documentation for [Archicad MCP](https://github.com/alesdev88/Archicad-MCP), an
MCP server for a running Archicad 29 or 30. Start with the
[README](https://github.com/alesdev88/Archicad-MCP#readme); these are the
reference documents it links to.

- **[Install](install.md)**: every install route, the Tapir installer, and the
  settings.
- **[Tools](tools.md)**: every tool, what it checks before it writes and what it
  needs.
- **[API dashboard](api-dashboard.html)**: every command the server can reach,
  official JSON API and Tapir, grouped as the add-on groups them. Solid boxes
  have a dedicated tool; hollow boxes are reachable through the gateway. This is
  the one page here that is generated rather than written, by
  `scripts/build_dashboard.py`.
- **[Known issues](known-issues.md)**: the property-read crash, the element
  ceiling, verified property names, and what has been validated end to end.
- **[Finding elements](query.md)**: the `find_elements` criteria language,
  `search_definitions`, and what each one reads from Archicad.
- **[Writing rules](rules.md)**: every rule type, field, and the scoring model.
- **[Reading and writing element data](element-data.md)**: classifications,
  GDL and marker parameters and IFC properties next to property values, the
  addresses for each, units, and the readback.
- **[Schedules](schedules.md)** and **[schedule criteria codes](scheme-criteria-codes.md)**:
  the Scheme Settings XML round trip, and the empirical `Param_Type` and
  `Relation_Index` table.
- **[GDL pipeline](gdl-pipeline.md)**: mesh models to library parts with finish
  dropdowns, and the GDL fine print the generator encodes.
- **[Scripting](scripting.md)**: `run_script` and `apply_changeset`.
- **[Development](development.md)**: tests, Tapir definitions, bundles and
  releasing.

## Element creation tools

To change the Railing tool's own defaults so your next manual placement
inherits them, apply a Railing favorite to the tool defaults:

```
execute_write_api_command
  name:   ApplyFavoritesToElementDefaults
  params: {"favorites": ["<your railing favorite>"]}
```

This is separate from favoriteName on create_elements, which applies a
favorite to one new element and leaves the tool defaults as it found them.
