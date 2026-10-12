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

No sections of the trial markings were taken; the live check below measured the paint against the
surfaces from an IFC export instead, and the user's plan view of story 1 shows both sets.

## Live check (after the build)

12.10.2026, MCP-Test on port 19725 (Archicad 29, Tapir 1.5.10; port 19724 held the real project CVP and
was not touched), the build at dde91c9, then the no-sink fix acafd05 (below). The trial's surfaces were
made again from `tests/fixtures/road_marking/surfaces_mcp_test.json` 80 m north, on story 1, with a 5 by 5 m
regular grid Mesh (a saddle, 5 cm a metre squared) at x 630 to 635 and a Polyline source:

| Surface | GUID |
| --- | --- |
| Slab, Top, 3.25 | `62827FEB-2368-184B-9820-8B82E2EC1D90` |
| Slab, CoreBottom, 3.80 | `AFB5AF4C-E8F4-6343-9AEB-9796AE445247` |
| Mesh, crowned road | `2021B414-573E-DC4D-9C1E-58F58BDFD77A` |
| Mesh, crossing level lines | `D4DB880C-8083-F749-B27F-0886B2D6044B` |
| Mesh, road edge level lines | `2D9CEEF9-C2C0-D446-8D3C-A7D30EA5FCAC` |
| Mesh, regular grid | `A507A0CB-6E91-DB43-9F12-269E6A24D0DC` |
| Polyline with an arc | `6193D3B3-34D7-F54B-84E4-8BF6605F6D41` |

Every bay, stop and lettering preset, the zebra, a lane line from the Polyline and one on the grid, dry run
then write (floats as reported after the no-sink fix; every case also warns that draping replaced the
points' heights):

| Case | Element | Nodes (added) | Reach | Floats up to | Reads back | Warnings |
| --- | --- | --- | --- | --- | --- | --- |
| zebra | `78437388` | 5 (3) | -2 to 2 | 0 mm | 5231 Pedestrian crossing | none |
| bus_lane | `EE744E16` | 2 (0) | -0.075 to 2.8 | 0 mm | 5111-1 Bus lane line | none |
| taxi_lane | `162E1FA9` | 2 (0) | -0.075 to 3.125 | 32.73 mm | 5111-2 Taxi lane line | floats |
| taxi_lane_broken | `A0B29075` | 19 (17) | -0.075 to 3.125 | 58.88 mm | 5121-2 Taxi lane line, broken | floats; level lines cross |
| bus_lane_broken | `F392F46F` | 21 (19) | -0.075 to 2.8 | 42.83 mm | 5121-1 Bus lane line, broken | floats; level lines cross |
| lane_from_polyline | `2413D5BF` | 10 (7) | -0.075 to 0.075 | 5.97 mm | 5121 Broken divider, in settlements | none (story 1, the arc split in 5) |
| bus_stop_layby | `77CCB49E` | 5 (3) | -0.15 to 2.55 | 19.53 mm | 5333 Bus stop in a lay-by | floats |
| taxi_stop_layby | `ADC192A1` | 6 (4) | -0.15 to 2.875 | 18.07 mm | 5334 Taxi stop in a lay-by | floats |
| disabled | `DAFA4866` | 2 (0) | 0 to 5.45 | 0 mm | 5352 Disabled bay | none |
| disabled_pair | `6F4E4812` | 2 (0) | 0 to 5.45 | 0 mm | 5352-1 Disabled bays, shared zone | none |
| taxi_bay | `2999C079` | 2 (0) | 0 to 5.05 | 0 mm | 5351 Taxi bay | none |
| ev_bay | `218104A4` | 2 (0) | 0 to 5.05 | 0 mm | 5354 EV charging bay | none |
| disabled_parallel | `F40E94C9` | 2 (0) | 0 to 2.45 | 0 mm | 5352-2 Disabled bay, parallel | none |
| taxi_stop_carriageway | `8ACAD59E` | 2 (0) | -0.075 to 2.55 | 0 mm | 5334-1 Taxi stop on the carriageway | none |
| bus_stop_short | `7B9B017C` | 2 (0) | -0.15 to 3.05 | 0 mm | 5333-2 Bus stop on the carriageway | BUS (4.00 m) reaches past the 3.90 m path; partly off 0 to 3.9 (the end bars reach 5 cm past the Slab) |
| ev_partly_off | `C1EAB5C9` | 2 (0) | 0 to 5.05 | 0 mm | 5354 EV charging bay | partly off 0 to 3.0 |
| lane_on_grid | `54F4703E` | 10 (8) | -0.075 to 0.075 | 4.38 mm | 5121 Broken divider, in settlements | none |

- **Placement.** Every marking's bounding box bottom equals the surface height at its first node; the
  points' markings landed on story 1 from their z. The user's plan view of story 1 shows every layout,
  word and symbol where and how it should be (both sets: the live check and the trial).
- **Update in place.** The bus lane line draped again on its own path with a 0.12 m line: `updated`, 2
  nodes as before, BUS kept, reads Custom.
- **Paint against the surface** (the markings' undersides read from an IFC export, every point against
  the surface at its plan position). On the Slabs 0.0 mm everywhere. On the Meshes the paint sank up to
  4.2 mm (and 7.4 mm on a 2 cm grid of the tool's own heights): the station lines touched the surface only
  at the cross samples, and the pieces between nodes could stray below them by the tolerance. Fixed in
  acafd05 (a 5 cm check grid raising each segment by its deepest dip, two tests that failed first); the
  nine Mesh markings draped again in place and exported again: no underside point below its surface
  (lowest gap -0.0 mm), floats as in the table. The large floats are words and lines over a crown or a
  bump, where one straight cross fall spans up to 3.2 m: reported, as designed.
- **Regular grid against Archicad.** 50 triangles each, but Archicad chose the other diagonal in some
  cells (its rows mostly one way with the last column flipped and the top row reversed; the tool's
  alternate by row): worst 25.00 mm on 441 points, half the cells' twist. No rule to copy from one sample;
  the guide states the limit.
