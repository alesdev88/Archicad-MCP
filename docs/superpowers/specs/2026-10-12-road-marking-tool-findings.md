# Road Marking tool: trial findings (12.10.2026)

The trial run of `docs/superpowers/specs/2026-10-12-road-marking-tool-design.md`, in MCP-Test (port
19725, Archicad 29, Tapir 1.5.10; port 19724 held another instance with a modal dialog open, so
`list_instances` failed as a whole and the project name was checked on 19725 directly before every
write). Throwaway code in the session scratchpad; this note, the fixtures under
`tests/fixtures/road_marking/` and the spec amendments are what it leaves.

## Test elements in MCP-Test (story 1, level 3.00)

| Element | GUID | Where |
| --- | --- | --- |
| Slab A, reference Top, `level` 0.25 | `8BD33B9C-5B7D-5C46-8B28-9EC1CB9310CC` | x 560 to 580, y 40 to 52 |
| Slab B, reference CoreBottom, `level` 0.5 | `69820A4A-48C6-864D-9027-5FCC737FB250` | x 582 to 586, y 40 to 44 |
| Mesh, crowned road (12 cm crown on y 44, 2 % rise in x) | `0845E3E8-C013-B24B-B67B-07DE2CDB0469` | x 590 to 620, y 40 to 48 |
| Mesh, warped with crossing level lines | `3F00556B-5F8C-9246-93AF-8C8352FDA4CC` | x 590 to 620, y 50 to 58 |
| Mesh, warped with road edge level lines | `4B9EA3B5-C5A6-3B44-A871-04DC1E093CC3` | x 590 to 620, y 60 to 68 |
| Polyline with an arc (source) | `0A1DD004-0684-2E4D-887C-FC3A47756636` | x 591 to 619, y 63 to 65 |

The elements were made at y 0 to 28 and moved 40 m north after the IFC exports, clear of the
Road Markings on story 0 (they span x 520 to 732, y -17 to 21, not only x 520 to 550). The fixtures
hold the details as exported (before the move).

## Settled facts

1. **Slab top = `zCoordinate` + `offsetFromTop`.** `zCoordinate` is absolute (story level plus
   `level`). Three Slabs agree with `Get3DBoundingBoxes`: story 0 CoreTop (0 + 0.1 = 0.10), story 1
   Top (3.25 + 0 = 3.25), story 1 CoreBottom (3.5 + 0.3 = 3.80).
2. **Mesh point z is relative:** absolute = story level + `level` + z (the crowned Mesh's top 3.92 =
   3.00 + 0.2 + 0.72). Mesh details carry no `zCoordinate`, so the tool reads `GetStories` for the
   story's level. Outlines come back closed (the first point repeated at the end).
3. **Archicad triangulates a Mesh as a constrained Delaunay** of its outline, hole and level line
   points with the level lines as edges. Measured on a 0.25 m grid against Archicad's own triangles
   from an IFC export (`IFCFileOperation` save; Archicad puts every Mesh into one `IfcSite`):

   | Mesh | Plain Delaunay | Constrained Delaunay |
   | --- | --- | --- |
   | Crowned road (each half planar) | 120 mm off | 0.0 mm |
   | Road edge level lines, points not coplanar (4026 points) | 0.91 mm off | 0.0 mm |
   | Level lines crossing the first one | 89 mm off | 51 mm off near the crossings |

   Where level lines cross, Archicad keeps the earlier line as edges and leaves the crossing
   segments out without adding points (its 38 triangles are exactly what 32 points without extra
   points make), and two of its edges near the dropped segments are not locally Delaunay. The tool
   drops the same segments (outline first, then holes, then level lines in order) and warns; it does
   not try to copy Archicad's leftovers there.
4. **The pure Python triangulation reproduces Archicad's** on the crowned and road edge Meshes (0.00
   mm on every grid point) and drops the same three segments on the crossing one.
5. **Speed** (pure Python, this Mac): 966 points 0.07 s, 4958 points 0.35 s, 19681 points 1.7 s to
   triangulate; a height lookup takes 5 to 7 microseconds. Finding where a level line starts must walk
   round the vertex (a vertex to triangle map); scanning every edge took 2.05 s at 5000 points. One
   marking per call is fine up to about 20000 Mesh points; the batch form pays when one large Mesh
   carries many markings (100 markings on a 20000 point Mesh: about 3 minutes one by one).
6. **One `SetGDLParametersOfElements` call in the spec's order works:** `markingType` first, then
   the values that differ from the preset. The 5333-2 bus stop kept `letterOffset` 1.75 (the preset
   writes 1.50) and read back its preset name; `markingTypeDone` followed.
7. **`GetGDLParametersOfElements`** gives strings as text, arrays as lists (`dimension1` the length),
   the paint surface as an index (type `Material`), and single angles in radians (`s1Angle` written
   -30 read back -0.5236).
8. **Placement:** `CreateObjects` with only the name and the absolute coordinates sets dimensions 1
   by 1 by 1 and `useFixSize: true`, and puts a marking placed from points (no story given) on the
   story its z falls in (story 1 here). A Road Marking's `Get3DBoundingBoxes` top reads 1 m above its
   bottom (the part's nominal box), so the bounding box shows where a marking sits, not its paint.

## Design changes the trial asks for

1. **Mesh triangulation:** constrained Delaunay with the level lines as edges (the spec left plain or
   constrained open); crossing segments dropped in order with a warning.
2. **The fit never buries the paint.** A least squares line through a crown lies under the ridge
   (1.5 mm under it for a 15 cm line on a 3 % crown), so the paint's middle would sink into the road
   and vanish in renders. The station's line keeps the least squares cross fall but is raised until
   no lateral sample lies above it; `max_across_misfit_mm` then says how far the paint floats at most
   (2.25 mm at the edges of that line).
3. **Reading the surfaces** needs `GetStories` when a Mesh is draped (fact 2).
4. **Writing** is one call (fact 6); the spec's "one call or two" is settled.

## The trial markings (story 1)

| Case | Element | Result |
| --- | --- | --- |
| 5231 zebra across the crown, 0.2 m off the Mesh at each end | `E4306357-ECC6-D049-BDCC-8AB863D81303` | 3 nodes added (both edges and the crown, +0.12), cross fall 2.00 % from the 2 % rise, both overhangs reported off the surface |
| 5121 lane line on the crown, later updated to 0.12 m with no source | `EB458280-1969-8449-9783-8A636429396C` | rise 0.56 m over 28 m, no nodes added, on the ridge; the update draped its own path and reads Custom |
| 5121 lane line from the Polyline on the warped Mesh | `DFEE8146-EB9F-6746-9E3C-A961BF410D81` | arc split in 4, 5 nodes added, 4.73 mm along (tolerance 5), cross falls 1.3 to 4.3 %, story 1 from the source |
| 5352-1 disabled pair on Slab A | `0E5D7FCF-BAD8-CE4C-9E2E-C203F5E6484B` | reach 0 to 5.45, at 3.25 |
| 5333-2 bus stop with BUS moved to 1.75 | `0F3648B0-4EE8-414C-B523-DBEC2A5056B5` | reach -0.15 to 3.05, override kept |
| 5351 taxi bay | `5B4177F0-3F34-9949-BC3E-048BA0997322` | reach 0 to 5.05 |
| Parking 60 degrees, kerb drawn towards -x | `6CACD9BC-0E9B-074E-A1A2-04D4AD78B3EF` | reach 0 to 5.80, `s1Angle` read back in radians |
| 5354 EV bay half off Slab B | `5F88EA89-3991-694E-BD58-C3E0185571DD` | partly off the surface from 0 to 3 m, at 3.80 |

Every marking's bottom sits on its surface (bounding boxes: 3.34, 3.34, 3.3826, 3.25, 3.25, 3.25,
3.25, 3.80). Ten draft tests passed: the fixtures against Archicad's heights, the Slab tops, a plane
(no nodes, the right cross fall), a crown (misfit, no nodes, on the ridge), an arc on a saddle (nodes
added, arcs split in proportion, nodes on the arc), off and partly off stretches with lift, the write
order over a faked Archicad and the refusals. Sections across the markings: see "Sections" below.

## Sections

The user's section and 3D screenshots of the trial markings are the visual check; noted here when
they arrive.
