---
title: Archicad MCP docs
---

# Archicad MCP

Documentation for [Archicad MCP](https://github.com/alesdev88/Archicad-MCP), an
MCP server for a running Archicad 29. Start with the
[README](https://github.com/alesdev88/Archicad-MCP#readme); these are the
reference documents it links to.

- **[API dashboard](api-dashboard.html)**: every one of the 309 commands the
  server can reach, official JSON API and Tapir, grouped as the add-on groups
  them. Solid boxes have a dedicated tool; hollow boxes are reachable through
  the gateway. This is the one page here that is generated rather than written,
  by `scripts/build_dashboard.py`.
- **[Known issues](known-issues.md)**: the property-read crash, the element
  ceiling, verified property names, and what has been validated end to end.
- **[Writing rules](rules.md)**: every rule type, field, and the scoring model.
- **[Schedule criteria codes](scheme-criteria-codes.md)**: the empirical
  `Param_Type` and `Relation_Index` table, and how to extend it.
- **[GDL pipeline](gdl-pipeline.md)**: mesh models to library parts with finish
  dropdowns, and the GDL fine print the generator encodes.

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
