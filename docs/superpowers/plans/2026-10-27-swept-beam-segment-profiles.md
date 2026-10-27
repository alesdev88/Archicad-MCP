# Swept Beam: a profile per segment, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every segment of a Swept Beam use one of three Profile Manager profiles, chosen in the settings dialog or with a grip on the beam, without changing beams already placed.

**Architecture:** Three profile slots (`beamProfile`, `beamProfile2`, `beamProfile3`) and a per-segment slot array `segProfile`. The Master Script works out which slots are in use and each segment's profile attribute; the 3D script splits its runs where that attribute changes and reads each run's section on its own; the 2D script follows each segment's width; the Parameter Script keeps the array in step with node edits and steps a segment on a grip drag. `create_swept_beam` writes the new arrays and keeps the choices on an update.

**Tech Stack:** GDL (Archicad 27 converter, built from `gdl-src/swept-beam` by `archicad-gdl build-source`), Python 3 with pytest (`uv run pytest`), Tapir over the Archicad JSON API for live checks.

**Spec:** `docs/superpowers/specs/2026-10-27-swept-beam-segment-profiles-design.md`. It extends `docs/superpowers/specs/2026-09-29-swept-beam-design.md`; the gates it cites (A to G) are in `docs/superpowers/specs/2026-09-29-swept-beam-findings.md`.

## Global Constraints

- One `.gsm` for Archicad 27 to 30, built with the Archicad 27 converter. Never open and save the part in the Library Part Editor.
- The GUID in `libpart.toml` stays `8C2E4F71-3B9D-4A5E-B6C8-1D7F0A2E9B34`; `version` goes from 0.1.0 to 0.2.0.
- `beamProfile` keeps its name and type; only its description changes to "Profile 1".
- Beams placed before this change look and behave exactly as before: every segment on Profile 1, Profile 2 and 3 empty (0), no profile grips drawn.
- Helper grips use the value and marker pattern and never set a dragged value back (gate C).
- Work on branch `feature/swept-beam-segment-profiles` in a worktree. Never stage the main checkout's uncommitted `gdl-src/swept-beam/params.toml` change (`rectBMat` 36, the office template's steel); the worktree does not have it.
- Live checks write only to a project whose name starts with MCP-Test. Ports move on restart: run `list_instances` first and pick the port by project name. CVP and LZV are real projects: read only.
- No em or en dashes in anything written for Aleš (docs, comments, dialog text, commit messages).

## Review Focus

1. A beam placed with the 0.1.0 part (no `segProfile`, Profile 2 and 3 at 0) is opened after the update: expected identical 3D, plan and parameters, and no profile grips drawn. Pinned in Task 7, steps 1 and 2.
2. A profile change at a kink on a rising path: the overshoot past the mitre must stay inside the cut box, with no loose pieces (gate D). Pinned in Task 3, step 5.
3. A segment set to slot 3 after Profile 3 is emptied again: expected to show Profile 1, and the grip steps from it as if it were on Profile 1. Pinned in Task 2, step 4 and Task 3, step 5.
4. Arc segments: the grip sits on the arc, an insert on an arc keeps the profile on both halves, and a profile change where a line meets a tangent arc (not a kink) leaves no gap or overlap. Pinned in Task 2, step 4 and Task 3, step 5.
5. Left face or Right face with two profiles of different widths, with and without Flip: that face stays on the node line on both sides. Pinned in Task 3, step 5 and Task 4, step 4.

---

### Task 1: Parameters, version and the worktree

**Files:**
- Modify: `gdl-src/swept-beam/params.toml`
- Modify: `gdl-src/swept-beam/libpart.toml:3`
- Test: `tests/test_gdl_source.py`
- Add to the branch: the spec and this plan (untracked in the main checkout today)

**Interfaces:**
- Produces, in parameter-list order:
  - `beamProfile` (profile, default 1), description "Profile 1"
  - `beamProfile2` (profile, default 0), "Profile 2", right after `beamProfile`
  - `beamProfile3` (profile, default 0), "Profile 3", right after `beamProfile2`
  - `segProfile` (integer array, default `[1, 1]`), "Segment profile", right after `beamProfile3`
  - `segProfGrip` (length array, default `[0.0, 0.0]`, hidden), "Profile grip (helper)", after `extEndDone`
  - `segProfDone` (length array, default `[0.0, 0.0]`, hidden), "Profile grip applied (helper)", right after `segProfGrip`
  - `gripProfiles` (boolean, default true), "Profile grips", right after `gripCuts`

- [ ] **Step 1: Make the worktree**

```bash
cd "/Users/alesd/Developer/Archicad MCP"
git worktree add .claude/worktrees/segment-profiles -b feature/swept-beam-segment-profiles main
mv docs/superpowers/specs/2026-10-27-swept-beam-segment-profiles-design.md .claude/worktrees/segment-profiles/docs/superpowers/specs/
mv docs/superpowers/plans/2026-10-27-swept-beam-segment-profiles.md .claude/worktrees/segment-profiles/docs/superpowers/plans/
```

Every later path in this plan is relative to the worktree.

- [ ] **Step 2: Write the failing test**

```python
def test_swept_beam_has_three_profile_slots_and_segment_arrays():
    src = source.load_source(Path(__file__).resolve().parent.parent / "gdl-src" / "swept-beam")
    params = {p.name: p for p in src.params}
    order = [p.name for p in src.params]
    assert src.version == "0.2.0"
    assert (params["beamProfile"].type, params["beamProfile"].default) == ("profile", 1)
    assert params["beamProfile"].description == "Profile 1"
    for name in ("beamProfile2", "beamProfile3"):
        assert (params[name].type, params[name].default) == ("profile", 0)
    assert order.index("beamProfile2") == order.index("beamProfile") + 1
    assert order.index("beamProfile3") == order.index("beamProfile2") + 1
    assert (params["segProfile"].type, params["segProfile"].default) == ("integer", [1, 1])
    for name in ("segProfGrip", "segProfDone"):
        assert params[name].type == "length" and params[name].default == [0.0, 0.0]
        assert params[name].hidden
    assert params["gripProfiles"].type == "boolean" and params["gripProfiles"].default is True
```

(If `LibpartSource` names its version field differently, use that name; `test_version_param_takes_libpart_version` shows it.)

- [ ] **Step 3: Run it to make sure it fails**

Run: `uv run pytest tests/test_gdl_source.py::test_swept_beam_has_three_profile_slots_and_segment_arrays -v`
Expected: FAIL with `KeyError: 'beamProfile2'` or the version assertion.

- [ ] **Step 4: Add the parameters to `params.toml` and set `version = "0.2.0"` in `libpart.toml`**

Follow the file's own style: a comment above `beamProfile2` saying 0 is none, so a placed beam picks the slot up empty.

- [ ] **Step 5: Run the source tests and the build**

Run: `uv run pytest tests/test_gdl_source.py tests/test_gdl_cli_source.py -v`
Expected: all PASS.

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out /tmp/sb-build`
Expected: "built with the Archicad 27 converter" and "scripts interpret cleanly" for 27 and 29.

- [ ] **Step 6: Commit**

```bash
git add gdl-src/swept-beam/params.toml gdl-src/swept-beam/libpart.toml tests/test_gdl_source.py docs/superpowers/specs/2026-10-27-swept-beam-segment-profiles-design.md docs/superpowers/plans/2026-10-27-swept-beam-segment-profiles.md
git commit -m "feat: Swept Beam parameters for three profile slots and a profile per segment"
```

---

### Task 2: Master Script slots and the Parameter Script

**Files:**
- Modify: `gdl-src/swept-beam/scripts/master.gdl` (after the kink block)
- Modify: `gdl-src/swept-beam/scripts/param.gdl`
- Test: `tests/test_gdl_source.py`

**Interfaces:**
- Consumes: the Task 1 parameters.
- Produces, in `master.gdl` (so every script sees them):
  - `slotOn[1..3]`: 1 when the slot holds a profile of its own. `slotOn[1] = 1`; `slotOn[2] = 1` when `beamProfile2 > 0 AND beamProfile2 <> beamProfile`; `slotOn[3] = 1` when `beamProfile3 > 0 AND beamProfile3 <> beamProfile AND beamProfile3 <> beamProfile2`.
  - `nSlotsOn`: their sum.
  - `segAttr[1..nNode-1]`: the profile attribute index segment j uses. In "Rectangle" mode 0 for every segment. In "Profile attribute" mode `beamProfile`, or `beamProfile2` / `beamProfile3` when `segProfile[j]` is 2 / 3 and that slot is above 0. Guard reads with `j <= VARDIM1 (segProfile)` (a beam from before this change runs the Master Script before its Parameter Script pads the array).
  - `nodeBreak[1..nNode]`: 1 at nodes 2 to nNode - 1 where `segAttr[iN - 1] <> segAttr[iN]`, else 0.
  - `pgX[j]`, `pgY[j]`, `pgZ[j]`: the profile grip's home, two thirds along segment j (two thirds of the arc angle on an arc, using `segIsArc`, `segCx`, `segCy`), height `nodeZ[j] + (nodeZ[j + 1] - nodeZ[j]) * 2 / 3`.
  - `pgTx[j]`, `pgTy[j]`: the unit plan tangent there, pointing from node j to node j + 1 (the insert grip in `2d.gdl` shows the arc tangent's sign).
- Produces, in `param.gdl`: local copies `spf[]` (segProfile), `spg[]` (segProfGrip), `spd[]` (segProfDone), written back with the other arrays.

- [ ] **Step 1: Write the failing test**

Every place that shifts the per-segment arrays shifts the three new ones too. The test counts the shift lines by the existing `il` array:

```python
def test_swept_beam_param_script_shifts_the_profile_arrays_with_the_others():
    text = (Path(__file__).resolve().parent.parent / "gdl-src" / "swept-beam"
            / "scripts" / "param.gdl").read_text(encoding="utf-8")
    for name in ("spf", "spg", "spd"):
        assert text.count(f"{name}[k + 1] = {name}[k]") == text.count("il[k + 1] = il[k]")
        assert text.count(f"{name}[k] = {name}[k + 1]") == text.count("il[k] = il[k + 1]")
    assert '"segProfGrip", "segProfDone"' in text  # hidden in the dialog's parameter list
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest tests/test_gdl_source.py::test_swept_beam_param_script_shifts_the_profile_arrays_with_the_others -v`
Expected: FAIL (counts 0 against 2 and 1).

- [ ] **Step 3: Write the Master Script block and change the Parameter Script**

Master Script: the block above, with a header comment in the file's style.

Parameter Script, in its three sections:
- Top: add `"segProfGrip", "segProfDone"` to `HIDEPARAMETER`; hide `beamProfile2`, `beamProfile3`, `segProfile` and `gripProfiles` unless `profileMode = "Profile attribute"`; `VALUES "gs_ui_current_page" 1, 2, 3, 4, 5` (Task 5 adds a page); `VALUES{2} "segProfile" 1, "Profile 1", 2, "Profile 2", 3, "Profile 3"`. If the build or Archicad refuses `VALUES{2}` on an array, drop it: the repair clamp below keeps the values valid and the dialog shows plain numbers.
- Copy and repair: `spf[i]` from `segProfile` (1 past its end, and 1 for any value outside 1 to 3, read as `INT`), `spg[i]` and `spd[i]` from the helpers (0 past their ends).
- Fresh beam: `spf[i] = 1`, `spg[i] = 0`, `spd[i] = 0` for both nodes, beside the other resets.
- Insert on segment `jIns`: shift the three arrays with the others; the new segment `jIns + 1` gets `spf[jIns]` and zeroed helpers.
- Add past the end: the new last segment `n` gets `spf[n - 1]`; `spd[n] = spg[n]` like `idn[n] = il[n]`; the new last item `n + 1` gets `spf` 1 and zeroed helpers. Past the start: shift; the new first segment gets `spf[2]` (the old first); helpers zeroed.
- Merge: shift with the others; when `lenB > lenA` the remaining segment takes `spf[jDel]`, as `na[jDel - 1] = na[jDel]` does.
- The grip step, after the curve grips:

```
FOR j = 1 TO n - 1
    IF ABS (spg[j] - spd[j]) > GRIP_EPS THEN
        stCur = spf[j]
        IF slotOn[stCur] = 0 THEN stCur = 1
        stNext = stCur
        stTries = 0
        WHILE stTries < 3 DO
            stNext = stNext MOD 3 + 1
            stTries = stTries + 1
            IF slotOn[stNext] THEN stTries = 3
        ENDWHILE
        spf[j] = stNext
        spd[j] = spg[j]
    ENDIF
NEXT j
```

- Write back: `segProfile = ospf, segProfGrip = ospg, segProfDone = ospd` in the existing `PARAMETERS` line, each with exactly n items.

- [ ] **Step 4: Run the source tests and the build, then check it live**

Run: `uv run pytest tests/test_gdl_source.py -v`
Expected: all PASS, including the case clash test.

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out "$HOME/Documents/Archicad GDL Workspace"`
Expected: "scripts interpret cleanly" for 27 and 29.

Live, in MCP-Test (reload libraries with Tapir `ReloadLibraries`, or ask Aleš if it is refused):
1. `list_attributes` for Profile: pick three profiles with clearly different widths (P1, P2, P3). If MCP-Test has fewer than three, stop and ask Aleš to add them.
2. Place a beam with `create_swept_beam`, points (0,0,0), (4,0,0), (4,3,0), (0,3,0), profile `{"attribute": P1}`, `dry_run=false`. Then write `beamProfile2 = P2`.
3. Write `segProfGrip` as `[0, 0.3, 0, 0]`. Read back: `segProfile == [1, 2, 1, 1]`, `segProfDone[2] == 0.3`, nodes unchanged.
4. Same item again (0.6): `segProfile == [1, 1, 1, 1]` (only two slots on). Write `beamProfile3 = P3`, step segment 2 twice more: 2 then 3. Set `beamProfile3 = 0`: `segProfile[2]` still reads 3; one more step goes to 2 (from 3, read as 1). Set `beamProfile2 = beamProfile` (a duplicate) and step segment 3: it stays 1.
5. Put segment 2 on 2, then insert on it (`insL[2]` moved by 0.5): `segProfile == [1, 2, 2, 1, 1]`. Add past the end (`extEnd` up by 0.5): the new last item copies the old last segment. Drag node 3 onto node 2 (`nodeX`/`nodeY` of node 3 within 5 mm of node 2): the remaining segment keeps the longer neighbour's profile.
6. Repeat step 5's insert on a beam whose segment 2 is an arc (`segArc[2] = 90` degrees, in the unit Tapir takes for angle arrays, see Tapir API behaviour): both halves keep the profile.
7. Delete the test beams.

- [ ] **Step 5: Commit**

```bash
git add gdl-src/swept-beam/scripts/master.gdl gdl-src/swept-beam/scripts/param.gdl tests/test_gdl_source.py
git commit -m "feat: Swept Beam keeps a profile per segment through node edits and steps it with a grip"
```

---

### Task 3: 3D sections per run, the square joint and the 3D grip

**Files:**
- Modify: `gdl-src/swept-beam/scripts/3d.gdl`

**Interfaces:**
- Consumes: `segAttr[]`, `nodeBreak[]`, `slotOn[]`, `nSlotsOn`, `pgX/pgY/pgZ/pgTx/pgTy[]` from Task 2.
- Produces: subroutine `"loadSection"`: in `secAttr` (0 = rectangle); out `useProfile`, `nComp`, `compTypes`, `geo`, `compStart`, `compEnd`, `uLo`, `uHi`, `wLo`, `wTop`, `refU` (the code that today runs once at the top, with `beamProfile` replaced by `secAttr`).

- [ ] **Step 1: Move the section set-up into `"loadSection"`**

Today's top block (profile requests, rectangle, extents, Flip, reference line) becomes the subroutine. The `REQUEST{4} ("Profile_component_info", ...)` calls in the run loop use `secAttr` too. With Left face or Right face, `refU` now comes from each run's own section (spec: the face stays on the node line per segment).

- [ ] **Step 2: Split the runs and size the overshoot**

- A run ends at a node where `nodeKink[iN] OR nodeBreak[iN]`.
- First pass over the runs: `loadSection` with the run's `segAttr[nA]`; `secExtent` becomes the largest of the runs' extents. Second pass sweeps each run after its own `loadSection`.
- `cutAt` and the overshoot (`ovA`, `ovB` in `"putRun"`, `nCut` in the run loop) apply only where `nodeKink` is set. At a break that is not a kink there is no cut and no overshoot: the run ends on the node with TUBE's own square end, and both pieces end square to the same tangent at the same point.

- [ ] **Step 3: End cut grips and the 3D profile grip**

- Before the start cut grips run `loadSection` with `segAttr[1]`, before the end cut grips with `segAttr[nNode - 1]`, so the pivot and grips sit on that end's own section.
- When `gripProfiles AND profileMode = "Profile attribute" AND nSlotsOn >= 2`, for each segment j a length hotspot set like the 2D insert grip, in 3D at `pgX[j], pgY[j], pgZ[j]`, sliding along `(pgTx[j], pgTy[j], 0)`: base at home minus `segProfDone[j]` along the tangent, IDs `600000 + j * 10` (+1 reference, +2 moving), parameter `segProfGrip[j]`, description `"Segment " + STR (j, 1, 0) + ": Profile " + STR (s, 1, 0) + ", drag to switch"` where s is the segment's slot, read as 1 when that slot is off.

- [ ] **Step 4: Build**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out "$HOME/Documents/Archicad GDL Workspace"`
Expected: "scripts interpret cleanly" for 27 and 29.

- [ ] **Step 5: Check it live in MCP-Test (reload libraries, then `capture_window` of the 3D window)**

Use P1, P2 and P3 from Task 2. Each check is one beam; delete them afterwards.
1. The four-node loop with segment 2 on P2: segments 1 and 3 meet segment 2 on clean plumb mitres, each side in its own section.
2. The same loop with a 15 % slope (`slope_percent`): no loose pieces past the mitres (gate D).
3. A straight three-node beam, segment 2 on P2: the two pieces meet face to face at node 2, no gap, no overlap.
4. A line into a tangent arc (`segArc[2] = 90` degrees from a node where the line is tangent), the arc on P2: same as check 3.
5. Left face, then Right face, then Left face with Flip, on the loop with P1 and P2 of different widths: that face lies on the node line on every segment (compare with "Show reference axis" in plan).
6. Segment 2 set to slot 3 with `beamProfile3 = 0`: it shows P1 and the loop is one smooth run there.
7. The grips: 3D profile grip at two thirds of each segment; dragging one in 3D switches its segment. If a 3D drag does not commit the helper (Archicad drops it), record it in the findings and keep the plan grip only.

- [ ] **Step 6: Commit**

```bash
git add gdl-src/swept-beam/scripts/3d.gdl
git commit -m "feat: Swept Beam sweeps each segment in its own profile, mitred at corners, square elsewhere"
```

---

### Task 4: 2D footprint, end cut grips and the plan grip

**Files:**
- Modify: `gdl-src/swept-beam/scripts/2d.gdl`

**Interfaces:**
- Consumes: `segAttr[]`, `slotOn[]`, `nSlotsOn`, `pgX/pgY/pgTx/pgTy[]`, `nodePath[]`.
- Produces: per segment `segULo[j]`, `segUHi[j]` (the section's extent beside the axis with Flip and the reference line, as today's top block works it out, from `"Profile_default_boundingbox"` of `segAttr[j]`, or the rectangle for 0 or an unreadable profile).

- [ ] **Step 1: Per-segment extents**

Today's top block becomes a subroutine called per segment (or once per distinct `segAttr`).

- [ ] **Step 2: Footprint by segment**

Sample iP belongs to segment j when `nodePath[j] <= iP < nodePath[j + 1]` (the last sample to the last segment). Walking forward on the `uHi` side: at a node sample `nodePath[iN]` (iN from 2 to nNode - 1) where the two segments' extents differ, emit `"fpPoint"` twice, first with segment iN - 1's `fpU`, then with segment iN's. Walking back on the `uLo` side, the other way round. `"fpPoint"` itself stays as it is: its mitre offset puts either side's point on the mitre line at a kink, and square to the path elsewhere. The first and last points use segment 1 and the last segment.

- [ ] **Step 3: End cut grips and the plan profile grip**

- The start cut grips use segment 1's extents, the end cut grips the last segment's.
- When `gripProfiles AND profileMode = "Profile attribute" AND nSlotsOn >= 2`: per segment the insert grip's pattern at home `(pgX[jS], pgY[jS])` sliding along `(pgTx[jS], pgTy[jS])`, IDs `500000 + jS * 10` (+1, +2), parameter `segProfGrip[jS]`, marker `segProfDone[jS]`, the same description as the 3D grip.

- [ ] **Step 4: Build and check it live (`capture_window` of the plan)**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out "$HOME/Documents/Archicad GDL Workspace"`
Expected: "scripts interpret cleanly" for 27 and 29.

Live, with "Show plan fill" on and a visible hatch: the loop with P1 and P2 of different widths, the fill steps exactly where the projection does, at a corner and at a straight break. Then Left face and Right face with Flip: the fill matches the projection. The plan grip sits at two thirds of each segment, apart from the curve grip (middle) and the insert grip (one third); dragging it switches that segment. A beam with only Profile 1 set shows no profile grips.

- [ ] **Step 5: Commit**

```bash
git add gdl-src/swept-beam/scripts/2d.gdl
git commit -m "feat: Swept Beam plan follows each segment's width and gets a profile grip"
```

---

### Task 5: The Segments page

**Files:**
- Modify: `gdl-src/swept-beam/scripts/ui.gdl`

**Interfaces:**
- Consumes: `beamProfile`, `beamProfile2`, `beamProfile3`, `segProfile`, `gripProfiles`, `slotOn[]`.

- [ ] **Step 1: Add page 3 "Segments" and renumber Floor plan to 4 and Ends to 5**

- The header comment says five pages.
- Page 2: the row label "Profile" becomes "Profile 1".
- Page 3, in "Profile attribute" mode: "Profile 1", "Profile 2", "Profile 3" pickers at y 44, 68, 92 (the same `UI_INFIELD` geometry as page 2); "Profile grips" checkbox at y 116; a list `UI_LISTFIELD 1, 12, 142, 420, 96` with one row per segment, `UI_LISTITEM{2} j, 1, segProfile[j], 0, "", "Segment " + STR (j, 1, 0)` for j from 1 to `VARDIM1 (nodeX) - 1`.
- Page 3 in "Rectangle" mode: one note line, "Profiles per segment need the section source Profile attribute (Profile page)."
- Back and Next on page 3 as on the others.

- [ ] **Step 2: Build**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out "$HOME/Documents/Archicad GDL Workspace"`
Expected: "scripts interpret cleanly" for 27 and 29.

- [ ] **Step 3: Dialog check (Aleš's screenshots)**

Reload libraries in MCP-Test, put a test loop in place, and ask Aleš in text to open its settings on the Segments page and attach a screenshot. Check: three pickers, the checkbox, the list scrolls on a beam with more than four segments, a row set to 2 changes that segment. If the list rows do not edit, fall back to one `UI_INFIELD{3}` pop-up per segment with Next and Back for more than six segments, and record why in the findings.

- [ ] **Step 4: Commit**

```bash
git add gdl-src/swept-beam/scripts/ui.gdl
git commit -m "feat: Swept Beam Segments page with three profiles and a profile per segment"
```

---

### Task 6: `create_swept_beam` writes the new arrays and keeps the choices on an update

**Files:**
- Modify: `src/archicad_mcp/core/swept_beam.py`
- Modify: `docs/swept-beam.md`
- Test: `tests/test_swept_beam_tool.py`

**Interfaces:**
- Produces:
  - `_kept_profiles(current: dict, n: int) -> tuple[list[int], int]`: the beam's own `segProfile` when `len(current["nodeX"]) == n` (read with `int()`, padded or cut to n items, values outside 1 to 3 as 1), else `[1] * n` and the count of segments (items 1 to m - 1, m the old node count) that were on 2 or 3. A beam with no `segProfile` reads as all 1.
  - `_node_params(local, arcs, tol, items=False, radians=False, profiles: list[int] | None = None)`: writes `segProfile` (`profiles` or `[1] * n`), `segProfGrip` and `segProfDone` (`[0.0] * n`) right after `insDone`.
- In `_create`: on an update, read `current = pp.gdl_parameters(conn, update_guid)` once, right after `pp.existing_frame`, for dry runs too; pass `_kept_profiles(current, len(local))[0]` to `_node_params`; when the count is above 0 add the warning `f"The new path has {n} nodes instead of {m}, so every segment goes back to Profile 1 ({lost} were on Profile 2 or 3)."`; the item-write branch reuses that `current` instead of reading again (a new beam still reads after `pp.place`).

- [ ] **Step 1: Write the failing tests**

```python
def test_new_beams_put_every_segment_on_profile_1_with_balanced_profile_grips():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}, {"x": 3, "y": 4, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts))
    assert params["segProfile"] == [1, 1, 1]
    assert params["segProfGrip"] == params["segProfDone"] == [0.0, 0.0, 0.0]


def test_kept_profiles_pad_clamp_and_count_what_goes_back():
    assert sb._kept_profiles({"nodeX": [0, 1, 2], "segProfile": [2, 7]}, 3) == ([2, 1, 1], 0)
    assert sb._kept_profiles({"nodeX": [0, 1], "segProfile": [3.0, 1.0]}, 2) == ([3, 1], 0)
    # the last item belongs to no segment
    assert sb._kept_profiles({"nodeX": [0, 1, 2], "segProfile": [2, 1, 3]}, 4) == ([1, 1, 1, 1], 1)
    # a beam from before profiles per segment
    assert sb._kept_profiles({"nodeX": [0, 1]}, 2) == ([1, 1], 0)


def test_an_update_keeps_the_segment_profiles_when_the_node_count_holds():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
                  "GetGDLParametersOfElements": _gdl(nodeX=[0.0, 3.0, 3.0], segProfile=[1, 2, 1]),
                  "SetGDLParametersOfElements": OK})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}, {"x": 3, "y": 4, "z": 0}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert _written(conn)["segProfile"] == [1, 2, 1]
    assert result["warnings"] == []


def test_an_update_with_another_node_count_says_which_segments_go_back_to_profile_1():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
                  "GetGDLParametersOfElements": _gdl(nodeX=[0.0, 3.0, 3.0], segProfile=[2, 3, 1])})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}, {"x": 3, "y": 4, "z": 0},
           {"x": 0, "y": 4, "z": 0}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="b-1")
    assert _params(result)["segProfile"] == [1, 1, 1, 1]
    assert ("The new path has 4 nodes instead of 3, so every segment goes back to Profile 1 "
            "(2 were on Profile 2 or 3).") in result["warnings"]


def test_item_writes_put_the_kept_profiles_back_after_the_reset():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
                  "GetAddOnVersion": V172, "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _gdl(nodeX=[0.0, 3.0, 6.0], extEnd=0.0,
                                                     segProfile=[1, 3, 1])})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}, {"x": 6, "y": 1, "z": 0}]
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    params = [p for c, p in conn._core.calls if c == "SetGDLParametersOfElements"][0][
        "elementsWithGDLParameters"][0]["gdlParameters"]
    names = [p["name"] for p in params]
    assert names.index("aDone") < names.index("segProfile")
    assert {"name": "segProfile", "value": [1, 3, 1]} in params
    assert [c for c, _ in conn._core.calls].count("GetGDLParametersOfElements") == 1
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_swept_beam_tool.py -v`
Expected: the five new tests FAIL (`KeyError: 'segProfile'`, `AttributeError: _kept_profiles`).

- [ ] **Step 3: Implement `_kept_profiles`, the `profiles` argument and the update read in `swept_beam.py`**

The update tests that do not fake `GetGDLParametersOfElements` today (`test_update_uses_the_existing_frame`, `test_update_keeps_the_section_unless_the_call_sets_it`, `test_update_keeps_the_end_cuts_unless_the_call_sets_them`, `test_update_keeps_the_reference_line_unless_the_call_sets_it`) get `"GetGDLParametersOfElements": _gdl(nodeX=[0.0, 3.0])` in their fakes. `test_commit_places_and_writes_parameters` keeps its first nine names; the new arrays come after `insDone`.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Docs**

In `docs/swept-beam.md`: a "Profiles per segment" section after "Profile" (three profiles on the Segments page, the list, the grip two thirds along each segment that steps 1, 2, 3, grips only with two different profiles set, a corner keeps its mitre, elsewhere the pieces meet square, Rectangle, offset and Flip stay whole-beam), a Draw bullet for the grip, the Grips line naming the Profiles checkbox, and the `update_guid` line: it keeps the segment profiles when the node count stays the same, and says so when they go back to Profile 1.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/swept_beam.py tests/test_swept_beam_tool.py docs/swept-beam.md
git commit -m "feat: create_swept_beam writes segment profiles and keeps them on an update"
```

---

### Task 7: Acceptance in Archicad

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-swept-beam-findings.md` (a "Profiles per segment, 2026-10-27" section)

- [ ] **Step 1: Before installing the new build, record an old beam**

With the 0.1.0 part still loaded in MCP-Test: place a profiled beam (P1) with five nodes, one arc, a slope and an end cut, or use one that is there. Save its GDL parameters (`GetGDLParametersOfElements`) and a `capture_window` of 3D and plan to the scratchpad.

- [ ] **Step 2: Install the branch build and compare**

Build into `~/Documents/Archicad GDL Workspace`, reload libraries. The old beam's node arrays, cuts, profile and A read the same; `segProfile` reads all 1 with n items; 3D and plan match the captures; no profile grips.

- [ ] **Step 3: The cantilever case**

A U-shaped edge (three segments, open, a 2 % slope), P1 everywhere and P2 on the front segment, set from the dialog list once and with the plan grip once. Mitred corners, correct plan fill, Left face on the slab edge line.

- [ ] **Step 4: `create_swept_beam` round trip**

`update_guid` on that beam with the same points and `dry_run=false`: the front segment stays on P2. Again with one more point: the warning names 1 segment, and every segment reads 1. This also shows whether Tapir writes the integer array item by item; if it fails, stop and report the error (do not change the parameter type).

- [ ] **Step 5: Archicad 27**

If MCP-Test_27 is open (`list_instances`), repeat step 3 there with `capture_window`, and ask Aleš for a screenshot of the Segments page in 27. If no Archicad 27 is running, ask Aleš whether to open it or skip.

- [ ] **Step 6: Write down what was found, delete the test beams, commit**

What worked, what fell back (`VALUES{2}` on the array, the list rows, the 3D drag), with dates, in the findings doc.

```bash
git add docs/superpowers/specs/2026-09-29-swept-beam-findings.md
git commit -m "docs: Swept Beam profiles per segment, live findings"
```

---

### Task 8: Merge and deploy (only after Aleš says so)

**Files:**
- In the Archicad GDL repo: `Library/Structure/Swept Beam/Swept Beam.gsm`, `Library/Structure/Swept Beam/source/`
- `~/Documents/Archicad GDL Workspace/Swept Beam.gsm`

- [ ] **Step 1: Ask Aleš whether to merge the branch into main and push**

Follow superpowers:finishing-a-development-branch. Nothing is pushed without his yes.

- [ ] **Step 2: Build from the main checkout, which carries `rectBMat` 36**

After the merge, in `/Users/alesd/Developer/Archicad MCP` (main, with its uncommitted `params.toml` change still unstaged):

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out /tmp/sb-office`
Expected: "scripts interpret cleanly" for 27 and 29.

- [ ] **Step 3: Copy to the Library and the Workspace, add the preview**

```bash
cp /tmp/sb-office/"Swept Beam.gsm" "/Users/alesd/Documents/Archicad GDL/Library/Structure/Swept Beam/"
rsync -a --delete --exclude .DS_Store "/Users/alesd/Developer/Archicad MCP/gdl-src/swept-beam/" "/Users/alesd/Documents/Archicad GDL/Library/Structure/Swept Beam/source/"
cd "/Users/alesd/Documents/Archicad GDL/Library" && python3 tools/add_preview.py "Structure/Swept Beam/Swept Beam.gsm"
cp "/Users/alesd/Documents/Archicad GDL/Library/Structure/Swept Beam/Swept Beam.gsm" "$HOME/Documents/Archicad GDL Workspace/"
```

Expected: `diff -rq` of `gdl-src/swept-beam` against `Library/Structure/Swept Beam/source` prints nothing; `add_preview.py` reports only the picture changed.

- [ ] **Step 4: Commit in the Archicad GDL repo**

```bash
cd "/Users/alesd/Documents/Archicad GDL"
git add "Library/Structure/Swept Beam"
git commit -m "feat: Swept Beam 0.2.0 in the Library, a profile per segment"
```

Then tell Aleš to reload libraries in any open project.
