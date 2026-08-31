"""GDL library-part authoring pipeline.

Turns mesh models (Wavefront OBJ, Autodesk 3DS) into Archicad library parts:
parse -> optional Blender decimation -> HSF folder -> LP_XMLConverter compile
-> deploy into a running Archicad via the same connection layer the MCP
server uses. Entry point: `archicad-gdl` (see archicad_mcp.gdl.cli).
"""
