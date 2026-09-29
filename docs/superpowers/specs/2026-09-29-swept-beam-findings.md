# Swept Beam: gate findings

## Gate A

Run on 2026-09-29. Archicad 29 build 5101 (`MCP-Test`, port 19724) and, at the
user's request, mainly Archicad 27 build 6080 (`MCP-Test_27`, port 19725, Tapir
1.5.9). The GDL workspace folder was not loaded in either project; it was added
with Tapir `AddLibraries` (undo: remove it in Library Manager).

1. Build into the workspace: clean validation with the 27 and 29 converters.
2. Place and render (29): a straight 3 m beam, 0.2 x 0.2, building material 1.
3. Helix through the API: arrays read back exactly as written. The Parameter
   Script runs during an API write but not after the last parameter: the helper
   arrays were parked with segment 2 still straight. Decision: the MCP tool
   writes the helper arrays itself, last (already in the plan).
   Units: `SetGDLParametersOfElements` passes numbers through unchanged, and
   Archicad takes a single Angle parameter in degrees but Angle ARRAY items in
   radians (both read back in radians). The first helix was sent in degrees, so
   `segArc[2]` became 5156.62 degrees: the script treated it as straight and the
   roll of "30" was a 1719 degree twist, which looked like scalloped wedges.
   Resent in radians (arc pi/2, roll pi/6): a clean quarter helix rising 1.0 m, a
   45 degree mitre at node 2, a smooth twist to 30 degrees, and the Parameter
   Script parks the helpers exactly as offline (`segSag[2] = 0.6213`, insert grip
   at (3.549, 0.951)). A roll-per-beam change made on the wrong reading was
   reverted; per-node roll stays as specified. Decision: the MCP tool sends
   `segArc` and `nodeRoll` in radians.
   `A` is not the part's default (3) after an API placement but the Object tool's
   current default size (0.7 in 29, 0.6 in 27), and the Parameter Script does not
   run on placement. On a two-node beam the next edit would snap node 2 to that
   `A`. To be settled with the placement method at gate C.
5. Absolute z: `CreateObjects` at z 5.0 on story 1 (level 4.0) reads back
   `origin.z = 5.0`, `floorIndex = 1`: z is absolute. Probe deleted.
7. Archicad 27 opens, places and renders the 27-format part.
