# GDL library-part pipeline

`archicad-gdl` turns mesh models (Wavefront OBJ, Autodesk 3DS) into Archicad
library parts (.gsm) and deploys them into a running Archicad, without ever
opening the GDL editor. It lives in `archicad_mcp.gdl` and shares the
connection layer with the MCP server.

```bash
archicad-gdl build model.3ds --name "My Chair" --config assets.json --out build
archicad-gdl deploy "build/My Chair.gsm" --place 0 0 --preview check.png
archicad-gdl inspect model.3ds
```

## What build does

1. **Parse** the mesh. Units (mm/cm/m) are autodetected from the extent.
   3DS input additionally gets keyframer pivot correction (exporters park
   sub-meshes at the origin and place them via the keyframer), duplicate-mesh
   removal, vertex welding (3DS meshes ship as unstitched patches whose seams
   would render as visible edges across smooth surfaces), and degenerate-face
   removal.
2. **Decimate** (optional, per-object config, needs Blender). Runs a separate
   background Blender process; an interactively open Blender is untouched.
   Targets are face counts per material-name substring; `0` means weld only.
   Do not decimate visible surfaces with gentle curvature (a tabletop's wide
   bevel): collapsing them into irregular triangles smears smooth shading
   into dark/light blotches. Frames, tubes and hidden parts decimate well.
3. **Generate HSF** (Hierarchical Symbol Format): `libpartdata.xml`,
   `ancestry.xml`, `paramlist.xml`, `libpartdocs.xml`, `scripts/*.gdl`.
   Each material group becomes one GDL body with per-group surface override
   parameters; finish variants become a "Finish" dropdown (and frame
   variants a "Frame finish" dropdown) via value-list scripts. Texture
   images are written to a `textures/` folder next to the .gsm (downscaled
   to 1024 px, named with a content hash so identical files dedupe across
   objects); they must be deployed into a loaded library together with the
   .gsm. Archicad could read pictures embedded inside the .gsm, but
   external render engines (Enscape, Twinmotion) cannot, so the pipeline
   ships textures as real library files that both can load.
4. **Compile** with LP_XMLConverter (`hsf2libpart`), located automatically in
   the newest Archicad bundle under `/Applications/Graphisoft` (override with
   the `LP_XMLCONVERTER` environment variable).
5. **Validate**: round-trip to XML and interpret the scripts
   (`convertlibrary -interpret`). A clean interpret run does NOT guarantee
   valid geometry; see the warning below.

## The one check that matters

Archicad silently drops defective 3D bodies (for example non-manifold edge
topology) while every offline validator passes them. The pipeline prevents
the known case (a GDL EDGE belongs to at most 2 polygons; junction edges are
split into paired instances), but after every deploy, render the placed
element and look at it:

```bash
archicad-gdl deploy out.gsm --place 0 0 --preview check.png
```

## Deployment and iteration

`deploy` embeds the .gsm into the open project's embedded library and
reloads. Tapir (1.5.3) cannot overwrite an existing embedded file, and fails
with a misleading "outputPath is not a valid relative path" error, so
iterating inside one project means fresh names each round.

The better setup for real work is a **linked library folder**: add your build
folder once via File > Libraries and Objects > Library Manager. From then on
`archicad-gdl build` overwrites the .gsm on disk with a stable GUID (pin it
in the config) and `ReloadLibraries` updates every placed instance in place.

## Asset config

Asset-specific knowledge lives in a JSON file, not in code; see the schema
example in `archicad_mcp/gdl/config.py`. Per object: a stable `guid`,
`textures` (shared), `variants` (each maps roles to a texture file or a flat
`[r, g, b]` color), `frame_variants`, `groups` (material-name substring to
label, texture role or `@frame`, fallback color), and `decimate` targets.

## GDL specifics baked into the generator

These were all found the hard way; the generator handles them, listed here
for anyone editing it:

- `TEVE` carries explicit UVs; mixing `VERT` and `TEVE` in one body silently
  disables all UVs. Groups without UVs use `VERT` and automatic wrapping.
- `DEFINE TEXTURE` references textures by file name; the file must exist in
  a loaded library (deploy pushes the `textures/` folder into the embedded
  library). A numeric expression would read a picture embedded in the .gsm,
  which Archicad renders but Enscape and other external engines cannot, so
  the pipeline does not use it.
- Edge status vocabulary: 0 visible sharp, 1 invisible sharp, 3 invisible
  smooth. Status 1 is used between faces of very different sizes so curved
  rims cannot smear shading across large flat faces.
- Ancestry uses the generic placeable Object subtype chain; "Missing
  ancestor" findings from offline validation are expected and filtered.
