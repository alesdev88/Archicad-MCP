# Swept Beam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A GDL library part "Swept Beam" that sweeps a Profile Manager profile or a rectangle along an editable path of nodes with plan arcs and linear rise, a build route that compiles hand-written library parts with a chosen Archicad converter, and an MCP tool `create_swept_beam` that turns a morph line, polyline, line, arc or 3D points into a Swept Beam.

**Architecture:** The library part source lives as plain files in `gdl-src/swept-beam/` (GDL scripts plus `libpart.toml` and `params.toml`). A new module `archicad_mcp.gdl.source` turns that folder into HSF, and `toolchain` compiles it with the Archicad 27 LP_XMLConverter so one `.gsm` opens in 27 to 30. The MCP side adds two core modules: `swept_path` (pure geometry: chaining, greedy fit, slope, grip helpers) and `swept_beam` (reads sources, places the object, writes its arrays).

**Tech Stack:** Python 3.12 (stdlib `tomllib`), FastMCP, pytest, GDL (Archicad 21 to 23 features only), LP_XMLConverter from Archicad 27 and 29, Tapir 1.5.10.

**Spec:** `docs/superpowers/specs/2026-09-29-swept-beam-design.md` (read it first; this plan argues from it).

**Deviation from the spec's rollout, approved by the user on 2026-09-29:** the build route comes before the spike, because the spike's objects compile through it. The spike itself is done as three live gates (A, B, C) on the first increments of the real part instead of separate throwaway objects. Every spike item from the spec maps to a gate step. A failed gate stops the plan and goes back to the user.

## Global Constraints

- Never use em dashes or en dashes anywhere: docs, code, comments, commit messages. Rewrite the sentence instead.
- Commit author: `Aleš Dolenec <285164556+alesdev88@users.noreply.github.com>` (use `git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit ...`). Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `feature/swept-beam`.
- Tests: `uv run pytest` from the repo root. Tests needing Archicad are marked `live` and excluded by default.
- Before ANY live write: call `list_instances` and confirm `project_name` is `MCP-Test` (Archicad 29) or `MCP-Test_27` (Archicad 27) on the port you are about to write to. Port 19723 is the CVP production model: never write there.
- Library part name: `Swept Beam`. MainGUID: `8C2E4F71-3B9D-4A5E-B6C8-1D7F0A2E9B34`. It never changes.
- Build target: Archicad 27 converter. The part must never be saved from a Library Part Editor.
- GDL: only commands available in Archicad 23 or earlier. GDL angles are degrees; Tapir arc angles and object angles are radians.
- Segment arc sign: positive `segArc` means the path turns left (counter-clockwise) along the segment and the arc bulges to the right of its chord. `segSag` is positive to the right of the chord.
- Lengths in metres. Heights: Tapir z values are absolute; `nodeZ` is relative to the object's own elevation.
- `create_swept_beam` defaults to `dry_run=True` and is annotated destructive.

## Review Focus

These input classes are implied by the spec but easy to leave untested. Each has a test or gate step in the task named.

1. A polyline arc from Tapir arrives in radians with Archicad's sign; the beam must turn the same way as the drawn polyline. Pinned by `test_polyline_arc_becomes_degrees_with_same_sign` (Task 9) and gate D step 3 (Task 10).
2. `update_guid` pointing at something that is not a Swept Beam must be refused, never overwritten. Pinned by `test_update_refuses_other_elements` (Task 9).
3. Points with repeats, or fewer than two distinct points, must give a clear error rather than a zero-length beam. Pinned by `test_duplicates_are_dropped` and `test_fewer_than_two_distinct_points` (Task 8).
4. "Swept Beam" not loaded in the project: dry run warns, commit refuses without placing anything. Pinned by `test_missing_library_part_warns_and_refuses` (Task 9).
5. Someone deletes one item from only one array in Archicad's array editor: the Parameter Script must repair the arrays and the beam must still draw. Pinned by gate C step 8 (Task 7).

## File Structure

| File | Responsibility |
|---|---|
| `src/archicad_mcp/gdl/toolchain.py` (modify) | Converter lookup by Archicad version; `compile_hsf` and `validate_gsm` take a version. |
| `src/archicad_mcp/gdl/source.py` (create) | Load a source folder (TOML plus scripts), write HSF, build and validate. |
| `src/archicad_mcp/gdl/cli.py` (modify) | `archicad-gdl build-source`. |
| `gdl-src/swept-beam/libpart.toml` (create) | Name, fixed GUID, version, build target. |
| `gdl-src/swept-beam/params.toml` (create) | Every parameter, in dialog order. |
| `gdl-src/swept-beam/scripts/master.gdl` (create) | Path sampling and end tangents. |
| `gdl-src/swept-beam/scripts/param.gdl` (create) | Repair, reactions (A, slope, arc grip, insert, merge), parking of helper arrays. |
| `gdl-src/swept-beam/scripts/3d.gdl` (create) | Sweep with `TUBE{2}`, materials, 3D grips. |
| `gdl-src/swept-beam/scripts/2d.gdl` (create) | `PROJECT2{3}`, axis, node heights, plan grips. |
| `gdl-src/swept-beam/scripts/ui.gdl` (create) | Three-page settings dialog. |
| `src/archicad_mcp/core/swept_path.py` (create) | Pure geometry for the MCP tool. |
| `src/archicad_mcp/core/swept_beam.py` (create) | `create_swept_beam` logic over a connection. |
| `src/archicad_mcp/server.py` (modify) | Register `create_swept_beam` in full mode. |
| `manifest.json`, `README.md`, `tests/test_tool_annotations.py` (modify) | Advertise and classify the tool. |
| `docs/swept-beam.md` (create) | How the team installs and uses the part. |
| `docs/superpowers/specs/2026-09-29-swept-beam-findings.md` (create) | Gate results. |
| `tests/test_gdl_toolchain.py` (modify), `tests/test_gdl_source.py`, `tests/test_gdl_cli_source.py`, `tests/test_swept_path.py`, `tests/test_swept_beam_tool.py` (create) | Tests. |

---

### Task 1: Converter lookup by Archicad version

**Files:**
- Modify: `src/archicad_mcp/gdl/toolchain.py:41-106`
- Test: `tests/test_gdl_toolchain.py`

**Interfaces:**
- Produces: `toolchain.find_lp_xmlconverter(version: int | None = None) -> Path`, `toolchain.installed_converter_versions() -> list[int]`, `toolchain.compile_hsf(hsf_dir, gsm_path, version: int | None = None) -> Path`, `toolchain.validate_gsm(gsm_path, extra_libs=None, version: int | None = None) -> list[str]`. Existing callers pass no version and keep today's behaviour.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_gdl_toolchain.py`)

```python
def _mac_install(root, *versions):
    rel = "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"
    return {v: _touch(root / f"Archicad {v}" / f"Archicad {v}.app" / rel) for v in versions}


def test_version_picks_that_release(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    exes = _mac_install(tmp_path / "Graphisoft", 27, 29)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    assert toolchain.find_lp_xmlconverter(27) == exes[27]
    assert toolchain.find_lp_xmlconverter() == exes[29]


def test_version_ignores_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    exes = _mac_install(tmp_path / "Graphisoft", 27)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    monkeypatch.setenv("LP_XMLCONVERTER", str(_touch(tmp_path / "other" / "LP_XMLConverter")))
    assert toolchain.find_lp_xmlconverter(27) == exes[27]


def test_missing_version_names_installed_ones(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    _mac_install(tmp_path / "Graphisoft", 27, 29)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    with pytest.raises(toolchain.ToolchainError, match="Archicad 26.*27, 29"):
        toolchain.find_lp_xmlconverter(26)


def test_installed_versions_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    _mac_install(tmp_path / "Graphisoft", 29, 27)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path / "Graphisoft"])
    assert toolchain.installed_converter_versions() == [27, 29]


def test_compile_hsf_uses_requested_version(tmp_path, monkeypatch):
    seen = {}

    def fake_find(version=None):
        seen["version"] = version
        return tmp_path / "LP"

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"gsm")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(toolchain, "find_lp_xmlconverter", fake_find)
    monkeypatch.setattr(toolchain.subprocess, "run", fake_run)
    toolchain.compile_hsf(tmp_path / "hsf", tmp_path / "out.gsm", version=27)
    assert seen["version"] == 27
```

Add `import subprocess` and `from pathlib import Path` at the top of the test file.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_gdl_toolchain.py -v`
Expected: the five new tests FAIL (`find_lp_xmlconverter() takes 0 positional arguments`, `installed_converter_versions` missing, `compile_hsf() got an unexpected keyword argument 'version'`).

- [ ] **Step 3: Implement** (replace `find_lp_xmlconverter`, and add the `version` argument to `compile_hsf` and `validate_gsm`)

```python
def _installed_converters() -> list[tuple[int, Path]]:
    """(Archicad version, converter binary) for every install found."""
    pattern, tail = _LP_LAYOUT.get(sys.platform, _LP_LAYOUT["darwin"])
    found = []
    for root in _archicad_roots():
        for entry in root.glob(pattern):
            exe = entry / tail
            if exe.is_file():
                m = re.search(r"Archicad (\d+)", entry.name)
                found.append((int(m.group(1)) if m else 0, exe))
    return found


def installed_converter_versions() -> list[int]:
    return sorted({v for v, _ in _installed_converters() if v})


def find_lp_xmlconverter(version: int | None = None) -> Path:
    """Locate LP_XMLConverter.

    With `version`, that Archicad release's converter. A library part keeps
    the format of the converter that compiled it and opens only in that
    release or a later one, so a part the team uses in Archicad 27 must be
    compiled by the 27 converter. The LP_XMLCONVERTER override is ignored
    then, because its release is unknown. Without `version`: the override
    first, then the newest installed release.
    """
    if version is None:
        env = os.environ.get("LP_XMLCONVERTER")
        if env:
            p = Path(env)
            if p.is_file():
                return p
            raise ToolchainError(f"LP_XMLCONVERTER points to a missing file: {env}")
    candidates = _installed_converters()
    if version is not None:
        for v, exe in candidates:
            if v == version:
                return exe
        installed = ", ".join(str(v) for v in installed_converter_versions()) or "none"
        raise ToolchainError(
            f"The Archicad {version} LP_XMLConverter was not found. Installed "
            f"releases with a converter: {installed}.")
    if not candidates:
        looked = ", ".join(str(r) for r in _archicad_roots())
        raise ToolchainError(
            f"LP_XMLConverter not found under {looked}. Install Archicad or set "
            "the LP_XMLCONVERTER environment variable to the binary inside the "
            "Archicad installation.")
    return max(candidates)[1]


def compile_hsf(hsf_dir: str | Path, gsm_path: str | Path,
                version: int | None = None) -> Path:
    """hsf2libpart: compile an HSF folder into a .gsm library part."""
    lp = find_lp_xmlconverter(version)
    result = subprocess.run(
        [str(lp), "hsf2libpart", str(hsf_dir), str(gsm_path)],
        capture_output=True, text=True, timeout=300)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not Path(gsm_path).exists():
        raise ToolchainError(f"hsf2libpart failed:\n{output}")
    return Path(gsm_path)
```

In `validate_gsm`, change the signature to `def validate_gsm(gsm_path, extra_libs=None, version: int | None = None) -> list[str]:` and its first line to `lp = find_lp_xmlconverter(version)`. Nothing else changes.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gdl_toolchain.py tests/test_gdl_tools.py -v`
Expected: all PASS (the GDL tool tests prove the unchanged default path still works).

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/toolchain.py tests/test_gdl_toolchain.py
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: choose the LP_XMLConverter by Archicad version

A part compiled by the 27 converter opens in 27 and every later release; a
29 part does not open in 27. compile_hsf and validate_gsm take the version;
without it they keep using the newest install.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Hand-written library parts to HSF

**Files:**
- Create: `src/archicad_mcp/gdl/source.py`
- Test: `tests/test_gdl_source.py`

**Interfaces:**
- Consumes: `toolchain.compile_hsf`, `toolchain.find_lp_xmlconverter`, `toolchain.installed_converter_versions` (Task 1); `generate.ANCESTRY_GUIDS`.
- Produces: `source.SourceError(ValueError)`, `source.Param`, `source.LibpartSource`, `source.load_source(root) -> LibpartSource`, `source.paramlist_xml(params: list[Param]) -> str`, `source.write_hsf(src: LibpartSource, hsf_dir) -> Path`.

Source folder layout: `libpart.toml`, `params.toml`, `scripts/{master,2d,3d,param,ui}.gdl` (2d and 3d required).

- [ ] **Step 1: Write the failing tests** (`tests/test_gdl_source.py`)

```python
"""Hand-written library parts: TOML plus GDL scripts to HSF."""

import subprocess
import textwrap
import xml.etree.ElementTree as ET

import pytest

from archicad_mcp.gdl import source, toolchain

LIBPART = textwrap.dedent("""\
    name = "Probe Part"
    guid = "5a1e7b3c-0d2f-4c6b-9e8a-7f1d2c3b4a50"
    version = "0.0.1"
    keywords = "probe"
    target_archicad = 27
    version_param = "sb_version"
""")

PARAMS = textwrap.dedent("""\
    [[param]]
    name = "A"
    type = "length"
    default = 1.0
    [[param]]
    name = "B"
    type = "length"
    default = 1.0
    [[param]]
    name = "ZZYZX"
    type = "length"
    default = 1.0
    [[param]]
    name = "nodeX"
    type = "length"
    default = [0.0, 3.0]
    description = "Node X"
    [[param]]
    name = "segArc"
    type = "angle"
    default = [15.0, 0.0]
    hidden = true
    [[param]]
    name = "beamProfile"
    type = "profile"
    default = 1
    [[param]]
    name = "rectBMat"
    type = "bmat"
    default = 1
    [[param]]
    name = "profileMode"
    type = "string"
    default = "Rectangle"
    [[param]]
    name = "flipProfile"
    type = "boolean"
    default = false
    [[param]]
    name = "pathTolerance"
    type = "length"
    default = 0.00001
    [[param]]
    name = "sb_version"
    type = "string"
    default = ""
    hidden = true
""")


def _write_source(root, libpart=LIBPART, params=PARAMS, scripts=None):
    scripts = scripts if scripts is not None else {
        "master.gdl": "EPS = 0.0001\n", "2d.gdl": "project2 3, 270, 2\n",
        "3d.gdl": "block 1, 1, 1\n", "param.gdl": "PARAMETERS A = A\n",
        "ui.gdl": 'UI_DIALOG "t"\n'}
    (root / "scripts").mkdir(parents=True)
    (root / "libpart.toml").write_text(libpart)
    (root / "params.toml").write_text(params)
    for name, text in scripts.items():
        (root / "scripts" / name).write_text(text)
    return root


def test_load_reads_metadata_params_and_scripts(tmp_path):
    src = source.load_source(_write_source(tmp_path))
    assert src.name == "Probe Part"
    assert src.guid == "5A1E7B3C-0D2F-4C6B-9E8A-7F1D2C3B4A50"
    assert src.target_archicad == 27
    assert [p.name for p in src.params][:4] == ["A", "B", "ZZYZX", "nodeX"]
    assert set(src.scripts) == {"master.gdl", "2d.gdl", "3d.gdl", "param.gdl", "ui.gdl"}


def test_version_param_takes_libpart_version(tmp_path):
    src = source.load_source(_write_source(tmp_path))
    assert next(p for p in src.params if p.name == "sb_version").default == "0.0.1"


def test_paramlist_scalar_array_and_flags(tmp_path):
    xml = source.paramlist_xml(source.load_source(_write_source(tmp_path)).params)
    assert '<Length Name="nodeX">' in xml
    assert '<ArrayValues FirstDimension="2" SecondDimension="0">' in xml
    assert '<AVal Row="1">0</AVal>' in xml and '<AVal Row="2">3</AVal>' in xml
    assert '<Angle Name="segArc">' in xml and "<ParFlg_Hidden/>" in xml
    assert '<Profile Name="beamProfile">' in xml
    assert '<BuildingMaterial Name="rectBMat">' in xml
    assert '<Value><![CDATA["Rectangle"]]></Value>' in xml
    assert '<Boolean Name="flipProfile">' in xml
    assert "<Value>0.00001</Value>" in xml


@pytest.mark.parametrize("entry, message", [
    ('[[param]]\nname = "extra"\ntype = "colour"\ndefault = 1.0\n', "unknown type"),
    ('[[param]]\nname = "2bad"\ntype = "length"\ndefault = 1.0\n', "not a valid GDL name"),
    ('[[param]]\nname = "mode"\ntype = "string"\ndefault = 3\n', "must be text"),
    ('[[param]]\nname = "flag"\ntype = "boolean"\ndefault = 2\n', "must be true or false"),
])
def test_bad_param_rejected(tmp_path, entry, message):
    with pytest.raises(source.SourceError, match=message):
        source.load_source(_write_source(tmp_path, params=PARAMS + entry))


def test_duplicate_name_rejected_case_insensitively(tmp_path):
    params = PARAMS + '[[param]]\nname = "NODEX"\ntype = "length"\ndefault = 1.0\n'
    with pytest.raises(source.SourceError, match="duplicate"):
        source.load_source(_write_source(tmp_path, params=params))


def test_empty_array_rejected(tmp_path):
    params = PARAMS + '[[param]]\nname = "empty"\ntype = "length"\ndefault = []\n'
    with pytest.raises(source.SourceError, match="empty array"):
        source.load_source(_write_source(tmp_path, params=params))


def test_a_b_zzyzx_must_come_first(tmp_path):
    params = PARAMS.replace('name = "A"', 'name = "Alpha"', 1)
    with pytest.raises(source.SourceError, match="A, B, ZZYZX"):
        source.load_source(_write_source(tmp_path, params=params))


def test_missing_3d_script_rejected(tmp_path):
    with pytest.raises(source.SourceError, match="3d.gdl"):
        source.load_source(_write_source(tmp_path, scripts={"2d.gdl": "x = 1\n"}))


def test_write_hsf_layout(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src",
                                           scripts={"2d.gdl": "a = 1\n", "3d.gdl": "b = 1\n"}))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    data = (hsf / "libpartdata.xml").read_text()
    assert "<MainGUID>5A1E7B3C-0D2F-4C6B-9E8A-7F1D2C3B4A50</MainGUID>" in data
    assert "Script_2D" in data and "Script_3D" in data
    assert "Script_1D" not in data and "Script_VL" not in data
    assert (hsf / "scripts" / "3d.gdl").read_text() == "b = 1\n"
    assert (hsf / "ancestry.xml").is_file() and (hsf / "paramlist.xml").is_file()


def test_param_script_goes_to_vl(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src"))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    assert (hsf / "scripts" / "vl.gdl").read_text() == "PARAMETERS A = A\n"
    assert (hsf / "scripts" / "1d.gdl").read_text() == "EPS = 0.0001\n"


@pytest.mark.skipif(27 not in toolchain.installed_converter_versions(),
                    reason="needs the Archicad 27 LP_XMLConverter")
def test_round_trip_through_archicad_27_converter(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src"))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    gsm = toolchain.compile_hsf(hsf, tmp_path / "part.gsm", version=27)
    back = tmp_path / "back"
    subprocess.run([str(toolchain.find_lp_xmlconverter(27)), "libpart2hsf", str(gsm), str(back)],
                   check=True, capture_output=True)

    # the converter rewrites numbers in its own notation (0.00001 comes back as
    # 1.e-5), so compare parsed parameters, not text
    def params(path):
        root = ET.fromstring(path.read_text(encoding="utf-8-sig"))
        out = []
        for el in root.find("Parameters"):
            flags = el.find("Flags")
            values = []
            for v in el.iter():
                if v.tag in ("Value", "AVal"):
                    try:
                        values.append(float(v.text))
                    except (TypeError, ValueError):
                        values.append(v.text)
            out.append((el.tag, el.get("Name"),
                        sorted(f.tag for f in flags) if flags is not None else [], values))
        return out

    assert params(back / "paramlist.xml") == params(hsf / "paramlist.xml")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_gdl_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'source'`.

- [ ] **Step 3: Implement** (`src/archicad_mcp/gdl/source.py`)

```python
"""Hand-written library parts: GDL script files plus TOML metadata to HSF.

The mesh route (generate.py) derives a part from a model. This route is for
parts written as GDL, like Swept Beam (gdl-src/swept-beam): the scripts live as
.gdl files, the parameters in params.toml, the identity in libpart.toml. The
parameter XML below was proven on 2026-09-29 by compiling with the Archicad 27
and 29 converters and converting back: arrays, Profile, BuildingMaterial,
Material, PenColor, LineType and hidden flags all survive unchanged.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from archicad_mcp.gdl.generate import ANCESTRY_GUIDS

PARAM_TAGS = {
    "length": "Length", "angle": "Angle", "real": "RealNum",
    "integer": "Integer", "boolean": "Boolean", "string": "String",
    "surface": "Material", "pen": "PenColor", "linetype": "LineType",
    "bmat": "BuildingMaterial", "profile": "Profile",
}
NUMERIC_TYPES = {"length", "angle", "real"}
INDEX_TYPES = {"integer", "surface", "pen", "linetype", "bmat", "profile"}

# (source script, HSF script file, libpartdata.xml section), in section order
SCRIPTS = (
    ("master.gdl", "1d.gdl", "Script_1D"),
    ("2d.gdl", "2d.gdl", "Script_2D"),
    ("3d.gdl", "3d.gdl", "Script_3D"),
    ("param.gdl", "vl.gdl", "Script_VL"),
    ("ui.gdl", "ui.gdl", "Script_UI"),
)
REQUIRED_FIRST = ("A", "B", "ZZYZX")
PARAM_KEYS = {"name", "type", "default", "description", "hidden", "child"}
HSF_VERSION = "46"  # both the 27 and 29 converters accept it and write their own format

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_GUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")

_PARAMLIST_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<ParamSection>
\t<ParamSectHeader>
\t\t<AutoHotspots>false</AutoHotspots>
\t\t<StatBits>
\t\t\t<STBit_UIDefault/>
\t\t</StatBits>
\t\t<WDLeftFrame>0</WDLeftFrame>
\t\t<WDRightFrame>0</WDRightFrame>
\t\t<WDTopFrame>0</WDTopFrame>
\t\t<WDBotFrame>0</WDBotFrame>
\t\t<LayFlags>65535</LayFlags>
\t\t<WDMirrorThickness>0</WDMirrorThickness>
\t\t<WDWallInset>0</WDWallInset>
\t</ParamSectHeader>
\t<Parameters SectVersion="27" SectionFlags="0" SubIdent="0">
"""


class SourceError(ValueError):
    """The library part source folder is incomplete or inconsistent."""


@dataclass
class Param:
    name: str
    type: str
    default: object
    description: str = ""
    hidden: bool = False
    child: bool = False

    @property
    def is_array(self) -> bool:
        return isinstance(self.default, list)


@dataclass
class LibpartSource:
    root: Path
    name: str
    guid: str
    version: str
    keywords: str
    target_archicad: int | None
    params: list[Param]
    scripts: dict[str, str] = field(default_factory=dict)


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        raise SourceError(f"Missing {path.name} in {path.parent}.")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"{path.name} is not valid TOML: {exc}") from exc


def _check_value(name: str, kind: str, value) -> None:
    if kind in NUMERIC_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SourceError(f"Parameter {name}: {kind} values must be numbers, got {value!r}.")
    elif kind in INDEX_TYPES:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SourceError(f"Parameter {name}: {kind} values must be whole numbers >= 0, got {value!r}.")
    elif kind == "boolean":
        if not (isinstance(value, bool) or value in (0, 1)):
            raise SourceError(f"Parameter {name}: boolean values must be true or false, got {value!r}.")
    elif kind == "string":
        if not isinstance(value, str):
            raise SourceError(f"Parameter {name}: string values must be text, got {value!r}.")
        if '"' in value or "]]>" in value:
            raise SourceError(f'Parameter {name}: string values cannot contain " or ]]>.')


def _param(entry: dict) -> Param:
    unknown = set(entry) - PARAM_KEYS
    if unknown:
        raise SourceError(f"Unknown keys in a [[param]] entry: {sorted(unknown)}.")
    name = str(entry.get("name", ""))
    if not _NAME.match(name):
        raise SourceError(f"{name!r} is not a valid GDL name (letters, digits, _; up to 32).")
    kind = str(entry.get("type", "")).lower()
    if kind not in PARAM_TAGS:
        raise SourceError(f"Parameter {name}: unknown type {kind!r}. Known: {sorted(PARAM_TAGS)}.")
    if "default" not in entry:
        raise SourceError(f"Parameter {name}: a default is required.")
    default = entry["default"]
    if isinstance(default, list):
        if not default:
            raise SourceError(f"Parameter {name}: an empty array needs at least one item.")
        for item in default:
            _check_value(name, kind, item)
    else:
        _check_value(name, kind, default)
    description = str(entry.get("description", ""))
    if '"' in description or "]]>" in description:
        raise SourceError(f'Parameter {name}: descriptions cannot contain " or ]]>.')
    return Param(name, kind, default, description,
                 bool(entry.get("hidden", False)), bool(entry.get("child", False)))


def _check_params(params: list[Param]) -> None:
    seen: set[str] = set()
    for p in params:
        key = p.name.casefold()  # GDL names are case-insensitive
        if key in seen:
            raise SourceError(f"Parameter {p.name}: duplicate name (GDL ignores case).")
        seen.add(key)
    first = [p.name for p in params[:3]]
    if first != list(REQUIRED_FIRST) or any(p.type != "length" or p.is_array for p in params[:3]):
        raise SourceError("The first three parameters must be A, B, ZZYZX, all single lengths.")


def load_source(root: str | Path) -> LibpartSource:
    root = Path(root)
    meta = _read_toml(root / "libpart.toml")
    raw = _read_toml(root / "params.toml")
    for key in ("name", "guid", "version"):
        if not str(meta.get(key, "")).strip():
            raise SourceError(f"libpart.toml needs '{key}'.")
    guid = str(meta["guid"]).upper()
    if not _GUID.match(guid):
        raise SourceError(f"libpart.toml guid is not a GUID: {meta['guid']!r}.")
    params = [_param(entry) for entry in raw.get("param", [])]
    _check_params(params)
    version_param = meta.get("version_param")
    if version_param:
        for p in params:
            if p.name == version_param:
                p.default = str(meta["version"])
    scripts = {}
    for src_name, _hsf_name, _section in SCRIPTS:
        path = root / "scripts" / src_name
        if path.is_file():
            scripts[src_name] = path.read_text(encoding="utf-8")
    if "2d.gdl" not in scripts or "3d.gdl" not in scripts:
        raise SourceError(f"scripts/2d.gdl and scripts/3d.gdl are required in {root}.")
    target = meta.get("target_archicad")
    return LibpartSource(root, str(meta["name"]), guid, str(meta["version"]),
                         str(meta.get("keywords", "")),
                         int(target) if target is not None else None, params, scripts)


def _number(value) -> str:
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _value_text(p: Param, value) -> str:
    if p.type == "string":
        return f'<![CDATA["{value}"]]>'
    if p.type == "boolean":
        return "1" if value else "0"
    if p.type in INDEX_TYPES:
        return str(int(value))
    return _number(value)


def paramlist_xml(params: list[Param]) -> str:
    rows: list[str] = []
    for p in params:
        tag = PARAM_TAGS[p.type]
        rows.append(f'\t\t<{tag} Name="{p.name}">')
        rows.append(f'\t\t\t<Description><![CDATA["{p.description}"]]></Description>')
        flags = [flag for flag, on in (("ParFlg_Child", p.child), ("ParFlg_Hidden", p.hidden)) if on]
        if flags:
            rows.append("\t\t\t<Flags>")
            rows += [f"\t\t\t\t<{flag}/>" for flag in flags]
            rows.append("\t\t\t</Flags>")
        if p.is_array:
            rows.append(f'\t\t\t<ArrayValues FirstDimension="{len(p.default)}" SecondDimension="0">')
            rows += [f'\t\t\t\t<AVal Row="{i}">{_value_text(p, v)}</AVal>'
                     for i, v in enumerate(p.default, 1)]
            rows.append("\t\t\t</ArrayValues>")
        else:
            rows.append(f"\t\t\t<Value>{_value_text(p, p.default)}</Value>")
        rows.append(f"\t\t</{tag}>")
    return _PARAMLIST_HEAD + "\n".join(rows) + "\n\t</Parameters>\n</ParamSection>\n"


def write_hsf(src: LibpartSource, hsf_dir: str | Path) -> Path:
    hsf_dir = Path(hsf_dir)
    scripts_dir = hsf_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for src_name, hsf_name, section in SCRIPTS:
        if src_name in src.scripts:
            (scripts_dir / hsf_name).write_text(src.scripts[src_name], encoding="utf-8")
            sections.append(f'\t<{section} SectVersion="20" SectionFlags="0" SubIdent="0"/>')
    (hsf_dir / "libpartdata.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<LibpartData Owner="0" Signature="1196644685" Version="{HSF_VERSION}">
\t<Identification>
\t\t<MainGUID>{src.guid}</MainGUID>
\t\t<IsPlaceable>true</IsPlaceable>
\t\t<IsArchivable>false</IsArchivable>
\t\t<MigrationValue>Normal</MigrationValue>
\t\t<IsTemplate>false</IsTemplate>
\t</Identification>
\t<Copyright SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<Ancestry SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<ParamSection SectVersion="27" SectionFlags="0" SubIdent="0"/>
{chr(10).join(sections)}
</LibpartData>
""", encoding="utf-8")
    ancestry = "\n".join(f"\t<MainGUID>{g}</MainGUID>" for g in ANCESTRY_GUIDS)
    (hsf_dir / "ancestry.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Ancestry>\n{ancestry}\n</Ancestry>\n',
        encoding="utf-8")
    (hsf_dir / "libpartdocs.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<libpartdocs>
\t<Copyright>
\t\t<Author>archicad-gdl</Author>
\t\t<License>
\t\t\t<Type>CC BY</Type>
\t\t\t<Version>4.0</Version>
\t\t</License>
\t</Copyright>
\t<Keywords SectVersion="1" SectionFlags="0" SubIdent="0">
\t\t<![CDATA[{src.keywords}]]>
\t</Keywords>
\t<CommentSection>
\t\t<![CDATA[{src.name} {src.version}, built by archicad-gdl build-source]]>
\t</CommentSection>
</libpartdocs>
""", encoding="utf-8")
    (hsf_dir / "paramlist.xml").write_text(paramlist_xml(src.params), encoding="utf-8")
    return hsf_dir
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gdl_source.py -v`
Expected: all PASS, and the round-trip test PASSES on this Mac (the 27 converter is installed).

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/source.py tests/test_gdl_source.py
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: write HSF from hand-written GDL library parts

A source folder holds the scripts as .gdl files plus libpart.toml and
params.toml. Arrays, Profile, building material and hidden parameters are
written in the XML shape the 27 and 29 converters round-trip unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `archicad-gdl build-source`

**Files:**
- Modify: `src/archicad_mcp/gdl/source.py` (add `SourceBuild`, `build_source`)
- Modify: `src/archicad_mcp/gdl/cli.py`
- Test: `tests/test_gdl_source.py`, `tests/test_gdl_cli_source.py`

**Interfaces:**
- Consumes: `load_source`, `write_hsf` (Task 2); `toolchain.compile_hsf`, `toolchain.validate_gsm`, `toolchain.installed_converter_versions` (Task 1).
- Produces: `source.SourceBuild` dataclass `(gsm: Path, name: str, guid: str, version: str, archicad: int | None, findings: dict[int, list[str]], skipped: list[int])`; `source.build_source(root, out_dir, archicad=None, validate_with=(27, 29), keep_hsf=False) -> SourceBuild`; CLI `archicad-gdl build-source SOURCE [--archicad N] [--out DIR] [--no-validate] [--keep-hsf]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdl_source.py`:

```python
def test_build_source_uses_target_and_validates_installed(tmp_path, monkeypatch):
    root = _write_source(tmp_path / "src")
    seen = {}

    def fake_compile(hsf_dir, gsm_path, version=None):
        seen["version"] = version
        seen["hsf_had_paramlist"] = (Path(hsf_dir) / "paramlist.xml").is_file()
        Path(gsm_path).write_bytes(b"gsm")
        return Path(gsm_path)

    monkeypatch.setattr(toolchain, "compile_hsf", fake_compile)
    monkeypatch.setattr(toolchain, "installed_converter_versions", lambda: [29])
    monkeypatch.setattr(toolchain, "validate_gsm", lambda gsm, extra_libs=None, version=None: [])
    result = source.build_source(root, tmp_path / "out")
    assert seen == {"version": 27, "hsf_had_paramlist": True}
    assert result.gsm == tmp_path / "out" / "Probe Part.gsm"
    assert result.findings == {29: []} and result.skipped == [27]
    assert not (tmp_path / "out" / "Probe Part").exists()  # HSF removed


def test_build_source_archicad_argument_wins(tmp_path, monkeypatch):
    root = _write_source(tmp_path / "src")
    seen = {}

    def fake_compile(hsf_dir, gsm_path, version=None):
        seen["v"] = version
        Path(gsm_path).write_bytes(b"")
        return Path(gsm_path)

    monkeypatch.setattr(toolchain, "compile_hsf", fake_compile)
    monkeypatch.setattr(toolchain, "installed_converter_versions", lambda: [])
    source.build_source(root, tmp_path / "out", archicad=29, validate_with=())
    assert seen["v"] == 29
```

Add `from pathlib import Path` to the imports of `tests/test_gdl_source.py`.

Create `tests/test_gdl_cli_source.py`:

```python
"""archicad-gdl build-source prints the build and each validation."""

from pathlib import Path

from archicad_mcp.gdl import cli, source


def test_build_source_prints_findings(tmp_path, monkeypatch, capsys):
    gsm = tmp_path / "Swept Beam.gsm"
    gsm.write_bytes(b"x" * 2048)
    seen = {}

    def fake_build(root, out_dir, archicad=None, validate_with=(27, 29), keep_hsf=False):
        seen.update(root=root, out=out_dir, archicad=archicad, validate_with=validate_with)
        return source.SourceBuild(gsm=gsm, name="Swept Beam", guid="G", version="0.1.0",
                                  archicad=27, findings={27: [], 29: ["warning: x"]}, skipped=[])

    monkeypatch.setattr(source, "build_source", fake_build)
    assert cli.main(["build-source", "gdl-src/swept-beam", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen["archicad"] is None and seen["validate_with"] == (27, 29)
    assert "built with the Archicad 27 converter" in out
    assert "validation (Archicad 27): scripts interpret cleanly" in out
    assert "warning: x" in out


def test_build_source_reports_source_errors(tmp_path, capsys):
    assert cli.main(["build-source", str(tmp_path / "missing")]) == 1
    assert "libpart.toml" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_gdl_source.py tests/test_gdl_cli_source.py -v`
Expected: FAIL (`build_source` missing; `invalid choice: 'build-source'`).

- [ ] **Step 3: Implement**

Append to `src/archicad_mcp/gdl/source.py` (and add `import shutil` plus `from archicad_mcp.gdl import toolchain` to its imports):

```python
@dataclass
class SourceBuild:
    gsm: Path
    name: str
    guid: str
    version: str
    archicad: int | None
    findings: dict[int, list[str]]
    skipped: list[int]


def build_source(root: str | Path, out_dir: str | Path, archicad: int | None = None,
                 validate_with: tuple[int, ...] = (27, 29),
                 keep_hsf: bool = False) -> SourceBuild:
    """Compile a source folder to <out_dir>/<name>.gsm and check its scripts.

    The converter is `archicad`, else libpart.toml's target_archicad, else the
    newest install. Validation runs once per listed release whose converter is
    installed; the others are reported as skipped, not failed.
    """
    src = load_source(root)
    target = archicad if archicad is not None else src.target_archicad
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hsf_dir = out_dir / src.name
    if hsf_dir.exists():
        shutil.rmtree(hsf_dir)
    gsm_path = out_dir / f"{src.name}.gsm"
    gsm_path.unlink(missing_ok=True)
    write_hsf(src, hsf_dir)
    try:
        gsm = toolchain.compile_hsf(hsf_dir, gsm_path, version=target)
    finally:
        if not keep_hsf:
            shutil.rmtree(hsf_dir, ignore_errors=True)
    installed = set(toolchain.installed_converter_versions())
    findings: dict[int, list[str]] = {}
    skipped: list[int] = []
    for v in validate_with:
        if v in installed:
            findings[v] = [ln.strip() for ln in toolchain.validate_gsm(gsm, version=v)]
        else:
            skipped.append(v)
    return SourceBuild(gsm, src.name, src.guid, src.version, target, findings, skipped)
```

In `src/archicad_mcp/gdl/cli.py`, add `from archicad_mcp.gdl import source as source_mod` to the imports, add the command:

```python
def _cmd_build_source(args) -> int:
    result = source_mod.build_source(
        args.source, args.out, archicad=args.archicad,
        validate_with=() if args.no_validate else (27, 29), keep_hsf=args.keep_hsf)
    target = f"the Archicad {result.archicad}" if result.archicad else "the newest"
    print(f"GSM: {result.gsm} ({result.gsm.stat().st_size // 1024} KB), "
          f"built with {target} converter")
    print(f"{result.name} {result.version}  GUID={result.guid}")
    for version, lines in sorted(result.findings.items()):
        if lines:
            print(f"validation (Archicad {version}):")
            for line in lines:
                print(f"  {line}")
        else:
            print(f"validation (Archicad {version}): scripts interpret cleanly")
    for version in result.skipped:
        print(f"validation (Archicad {version}): skipped, converter not installed")
    return 0
```

register it in `main` after the `build` parser:

```python
    p = sub.add_parser("build-source", help="hand-written GDL source folder -> .gsm")
    p.add_argument("source", help="folder with libpart.toml, params.toml, scripts/")
    p.add_argument("--archicad", type=int, default=None,
                   help="compile with this Archicad release's converter "
                        "(default: libpart.toml target_archicad, else newest)")
    p.add_argument("--out", default="build", help="output directory (default: build)")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--keep-hsf", action="store_true",
                   help="keep the intermediate HSF folder next to the .gsm")
    p.set_defaults(func=_cmd_build_source)
```

and widen the error handling in `main` to `except (toolchain.ToolchainError, source_mod.SourceError) as exc:`. Add `archicad-gdl build-source SOURCE [--archicad N] [--out DIR]` to the module docstring's usage block.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gdl_source.py tests/test_gdl_cli_source.py tests/test_gdl_toolchain.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/source.py src/archicad_mcp/gdl/cli.py tests/test_gdl_source.py tests/test_gdl_cli_source.py
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: archicad-gdl build-source compiles hand-written parts

Compiles with libpart.toml's target converter (or --archicad) and checks
the scripts with every installed converter of 27 and 29.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Swept Beam, first increment (path, rectangle sweep, plan, dialog, slope)

**Files:**
- Create: `gdl-src/swept-beam/libpart.toml`, `gdl-src/swept-beam/params.toml`
- Create: `gdl-src/swept-beam/scripts/master.gdl`, `param.gdl`, `3d.gdl`, `2d.gdl`, `ui.gdl`

**Interfaces:**
- Consumes: `archicad-gdl build-source` (Task 3).
- Produces: parameter names used by every later task and by `create_swept_beam`: `nodeX nodeY nodeZ nodeRoll segArc segSag insX insY slopePercent pathTolerance profileMode beamProfile rectW rectH rectBMat profileOffsetU profileOffsetW flipProfile overrideSurface surfaceOverride outlinePen showAxis axisPen axisLineType showNodeHeights textPen textSizeMm gs_ui_current_page sb_version A B ZZYZX`. Master Script variables used by 2D and 3D: `nNode nPath pathX[] pathY[] pathZ[] pathRoll[] startTX startTY startTZ endTX endTY endTZ`. Parameter Script subroutines `"seg"` (in: `jSeg`; out: `sChord sIsArc sRad sCx sCy sLen`) and `"park"` (in: `jSeg` after `"seg"`; out: `pkx pky`).

GDL rules for this task: one statement per line (no `:` separators, GDL uses `:` for labels); `!` comments; identifiers are case-insensitive, so no local may share a parameter's name in any case; angles in degrees.

- [ ] **Step 1: Write `gdl-src/swept-beam/libpart.toml`**

```toml
name = "Swept Beam"
guid = "8C2E4F71-3B9D-4A5E-B6C8-1D7F0A2E9B34"
version = "0.1.0"
keywords = "beam, curved beam, sloped beam, helix, profile, sweep"
target_archicad = 27
version_param = "sb_version"
```

- [ ] **Step 2: Write `gdl-src/swept-beam/params.toml`**

```toml
# Swept Beam parameters, in parameter-list order. See the spec, section 1.1.
# Types: length angle real integer boolean string surface pen linetype bmat profile.
# A list default makes an array parameter. All node arrays share one length.

[[param]]
name = "A"
type = "length"
default = 3.0
description = "Length (two-node straight beams)"

[[param]]
name = "B"
type = "length"
default = 0.2
description = "B (unused)"
hidden = true

[[param]]
name = "ZZYZX"
type = "length"
default = 0.2
description = "Height (unused)"
hidden = true

[[param]]
name = "nodeX"
type = "length"
default = [0.0, 3.0]
description = "Node X"

[[param]]
name = "nodeY"
type = "length"
default = [0.0, 0.0]
description = "Node Y"

[[param]]
name = "nodeZ"
type = "length"
default = [0.0, 0.0]
description = "Node height"

[[param]]
name = "nodeRoll"
type = "angle"
default = [0.0, 0.0]
description = "Node roll"

[[param]]
name = "segArc"
type = "angle"
default = [0.0, 0.0]
description = "Arc angle to the next node"

[[param]]
name = "slopePercent"
type = "real"
default = 0.0
description = "Slope for Apply slope (%)"

[[param]]
name = "pathTolerance"
type = "length"
default = 0.002
description = "Curve tolerance"

[[param]]
name = "segSag"
type = "length"
default = [0.0, 0.0]
description = "Arc grip (helper)"
hidden = true

[[param]]
name = "insX"
type = "length"
default = [1.0, 3.0]
description = "Insert grip X (helper)"
hidden = true

[[param]]
name = "insY"
type = "length"
default = [0.0, 0.0]
description = "Insert grip Y (helper)"
hidden = true

[[param]]
name = "profileMode"
type = "string"
default = "Rectangle"
description = "Section source"

[[param]]
name = "beamProfile"
type = "profile"
default = 1
description = "Profile"

[[param]]
name = "rectW"
type = "length"
default = 0.2
description = "Rectangle width"

[[param]]
name = "rectH"
type = "length"
default = 0.2
description = "Rectangle height"

[[param]]
name = "rectBMat"
type = "bmat"
default = 1
description = "Rectangle building material"

[[param]]
name = "profileOffsetU"
type = "length"
default = 0.0
description = "Section offset sideways"

[[param]]
name = "profileOffsetW"
type = "length"
default = 0.0
description = "Section offset up"

[[param]]
name = "flipProfile"
type = "boolean"
default = false
description = "Flip section"

[[param]]
name = "overrideSurface"
type = "boolean"
default = false
description = "Override surfaces"

[[param]]
name = "surfaceOverride"
type = "surface"
default = 1
description = "Override surface"
child = true

[[param]]
name = "outlinePen"
type = "pen"
default = 1
description = "Outline pen"

[[param]]
name = "showAxis"
type = "boolean"
default = false
description = "Show reference axis"

[[param]]
name = "axisPen"
type = "pen"
default = 1
description = "Axis pen"
child = true

[[param]]
name = "axisLineType"
type = "linetype"
default = 1
description = "Axis line type"
child = true

[[param]]
name = "showNodeHeights"
type = "boolean"
default = false
description = "Show node heights"

[[param]]
name = "textPen"
type = "pen"
default = 1
description = "Text pen"
child = true

[[param]]
name = "textSizeMm"
type = "real"
default = 2.0
description = "Text size (mm)"
child = true

[[param]]
name = "gs_ui_current_page"
type = "integer"
default = 1
description = "Dialog page"
hidden = true

[[param]]
name = "sb_version"
type = "string"
default = ""
description = "Swept Beam build"
hidden = true
```

- [ ] **Step 3: Write `gdl-src/swept-beam/scripts/master.gdl`**

```gdl
! Swept Beam -- Master Script
! Samples the node path into pathX/pathY/pathZ/pathRoll for the 2D and 3D
! scripts. Height and roll change linearly with plan length along each
! segment, so an arc with a rise is a helix (spec 1.4).

EPS = 0.0001
nNode = MIN (VARDIM1 (nodeX), VARDIM1 (nodeY), VARDIM1 (nodeZ), VARDIM1 (nodeRoll), VARDIM1 (segArc))
tolSafe = MAX (0.0001, pathTolerance)

DIM pathX[], pathY[], pathZ[], pathRoll[]
nPath = 0
startTX = 1
startTY = 0
startTZ = 0
endTX = 1
endTY = 0
endTZ = 0

FOR iSeg = 1 TO nNode - 1
    x1 = nodeX[iSeg]
    y1 = nodeY[iSeg]
    z1 = nodeZ[iSeg]
    r1 = nodeRoll[iSeg]
    x2 = nodeX[iSeg + 1]
    y2 = nodeY[iSeg + 1]
    z2 = nodeZ[iSeg + 1]
    r2 = nodeRoll[iSeg + 1]
    dxs = x2 - x1
    dys = y2 - y1
    chord = SQR (dxs * dxs + dys * dys)
    angSeg = segArc[iSeg]
    isArc = 0
    IF ABS (angSeg) > 0.01 AND ABS (angSeg) < 359.9 AND chord > EPS THEN isArc = 1

    nStep = 1
    segLen = chord
    IF isArc THEN
        radSeg = chord / (2 * SIN (ABS (angSeg) / 2))
        stepMax = 10
        IF tolSafe < radSeg THEN stepMax = 2 * ACS (1 - tolSafe / radSeg)
        nStep = MAX (1, CEIL (ABS (angSeg) / MAX (0.5, stepMax)))
        ! centre: from the chord midpoint along the left normal, signed by the arc
        dCen = chord / 2 * COS (angSeg / 2) / SIN (angSeg / 2)
        cenX = (x1 + x2) / 2 - dys / chord * dCen
        cenY = (y1 + y2) / 2 + dxs / chord * dCen
        vX = x1 - cenX
        vY = y1 - cenY
        segLen = radSeg * ABS (angSeg) * PI / 180
    ENDIF
    nStep = MAX (nStep, CEIL (ABS (r2 - r1) / 5))
    nStep = MIN (nStep, 720)

    ! unit plan tangents at the segment ends, and the rise per plan metre
    IF isArc THEN
        sgnArc = SGN (angSeg)
        t1x = -vY / radSeg * sgnArc
        t1y = vX / radSeg * sgnArc
        t2x = -(y2 - cenY) / radSeg * sgnArc
        t2y = (x2 - cenX) / radSeg * sgnArc
    ELSE
        t1x = 1
        t1y = 0
        IF chord > EPS THEN
            t1x = dxs / chord
            t1y = dys / chord
        ENDIF
        t2x = t1x
        t2y = t1y
    ENDIF
    riseSeg = 0
    IF segLen > EPS THEN riseSeg = (z2 - z1) / segLen
    IF iSeg = 1 THEN
        startTX = t1x
        startTY = t1y
        startTZ = riseSeg
    ENDIF
    endTX = t2x
    endTY = t2y
    endTZ = riseSeg

    FOR kStep = 0 TO nStep
        tt = kStep / nStep
        IF isArc THEN
            th = angSeg * tt
            px = cenX + vX * COS (th) - vY * SIN (th)
            py = cenY + vX * SIN (th) + vY * COS (th)
        ELSE
            px = x1 + dxs * tt
            py = y1 + dys * tt
        ENDIF
        pz = z1 + (z2 - z1) * tt
        pr = r1 + (r2 - r1) * tt
        addIt = 1
        IF nPath > 0 THEN
            dd = SQR ((px - pathX[nPath])^2 + (py - pathY[nPath])^2 + (pz - pathZ[nPath])^2)
            IF dd < 0.001 THEN addIt = 0
        ENDIF
        IF addIt THEN
            nPath = nPath + 1
            pathX[nPath] = px
            pathY[nPath] = py
            pathZ[nPath] = pz
            pathRoll[nPath] = pr
        ENDIF
    NEXT kStep
NEXT iSeg
```

- [ ] **Step 4: Write `gdl-src/swept-beam/scripts/param.gdl`** (first increment: repair, A, slope, parking, write back)

```gdl
! Swept Beam -- Parameter Script
! Runs after every edit. No Profile requests here (not allowed in this script).
! 1 repair the arrays, 2 react to the edit, 3 park the grip helpers, 4 write back.

VALUES "profileMode" "Rectangle", "Profile attribute"
VALUES "pathTolerance" RANGE [0.0005, 0.05]
VALUES "rectW" RANGE (0, ]
VALUES "rectH" RANGE (0, ]
VALUES "textSizeMm" RANGE (0, ]
VALUES "gs_ui_current_page" 1, 2, 3
HIDEPARAMETER "B", "ZZYZX", "segSag", "insX", "insY", "gs_ui_current_page", "sb_version"
IF profileMode <> "Profile attribute" THEN HIDEPARAMETER "beamProfile"
IF NOT (overrideSurface) THEN HIDEPARAMETER "surfaceOverride"
IF NOT (showAxis) THEN HIDEPARAMETER "axisPen", "axisLineType"
IF NOT (showNodeHeights) THEN HIDEPARAMETER "textPen", "textSizeMm"

EPS = 0.0001

! ---- 1. copy and repair: every array gets the length of nodeX ----
DIM nx[], ny[], nz[], nr[], na[]
n = VARDIM1 (nodeX)
IF n < 1 THEN
    n = 2
    nx[1] = 0
    nx[2] = 3
    ny[1] = 0
    ny[2] = 0
    nz[1] = 0
    nz[2] = 0
    nr[1] = 0
    nr[2] = 0
    na[1] = 0
    na[2] = 0
ELSE
    FOR i = 1 TO n
        nx[i] = nodeX[i]
        ny[i] = 0
        IF i <= VARDIM1 (nodeY) THEN ny[i] = nodeY[i]
        nz[i] = 0
        IF i <= VARDIM1 (nodeZ) THEN nz[i] = nodeZ[i]
        nr[i] = 0
        IF i <= VARDIM1 (nodeRoll) THEN nr[i] = nodeRoll[i]
        na[i] = 0
        IF i <= VARDIM1 (segArc) THEN na[i] = segArc[i]
    NEXT i
ENDIF
IF n = 1 THEN
    n = 2
    nx[2] = nx[1] + 1
    ny[2] = ny[1]
    nz[2] = nz[1]
    nr[2] = nr[1]
    na[2] = 0
ENDIF
na[n] = 0

! ---- 2. reactions ----
newA = A
IF n = 2 AND ABS (na[1]) < 0.01 THEN
    ddx = nx[2] - nx[1]
    ddy = ny[2] - ny[1]
    lenPlan = SQR (ddx * ddx + ddy * ddy)
    IF GLOB_MODPAR_NAME = "nodeX" OR GLOB_MODPAR_NAME = "nodeY" OR GLOB_MODPAR_NAME = "nodeZ" THEN
        newA = lenPlan
    ELSE
        IF ABS (A - lenPlan) > 0.0005 AND A > EPS THEN
            ux = 1
            uy = 0
            IF lenPlan > EPS THEN
                ux = ddx / lenPlan
                uy = ddy / lenPlan
            ENDIF
            nx[2] = nx[1] + ux * A
            ny[2] = ny[1] + uy * A
        ENDIF
    ENDIF
ENDIF

IF GLOB_UI_BUTTON_ID = 1 THEN
    sAcc = 0
    FOR i = 2 TO n
        jSeg = i - 1
        GOSUB "seg"
        sAcc = sAcc + sLen
        nz[i] = nz[1] + slopePercent / 100 * sAcc
    NEXT i
ENDIF

! (Task 7 adds the arc grip, insert and merge reactions here.)

! ---- 3. park the grip helpers ----
DIM sag[], ix[], iy[]
FOR j = 1 TO n
    sag[j] = 0
    ix[j] = nx[j]
    iy[j] = ny[j]
    IF j < n THEN
        jSeg = j
        GOSUB "seg"
        IF sIsArc THEN sag[j] = sChord / 2 * TAN (na[j] / 4)
        GOSUB "park"
        ix[j] = pkx
        iy[j] = pky
    ENDIF
NEXT j

! ---- 4. write back, with arrays of exactly n items ----
DIM ox[], oy[], oz[], orl[], oa[], os[], oix[], oiy[]
FOR i = 1 TO n
    ox[i] = nx[i]
    oy[i] = ny[i]
    oz[i] = nz[i]
    orl[i] = nr[i]
    oa[i] = na[i]
    os[i] = sag[i]
    oix[i] = ix[i]
    oiy[i] = iy[i]
NEXT i
PARAMETERS nodeX = ox, nodeY = oy, nodeZ = oz, nodeRoll = orl, segArc = oa, segSag = os, insX = oix, insY = oiy, A = newA
END

! ---- subroutines ----
"seg":
    ! plan geometry of segment jSeg (node jSeg to jSeg + 1)
    sdx = nx[jSeg + 1] - nx[jSeg]
    sdy = ny[jSeg + 1] - ny[jSeg]
    sChord = SQR (sdx * sdx + sdy * sdy)
    sIsArc = 0
    sRad = 0
    sCx = 0
    sCy = 0
    sLen = sChord
    IF ABS (na[jSeg]) > 0.01 AND ABS (na[jSeg]) < 359.9 AND sChord > EPS THEN
        sIsArc = 1
        sRad = sChord / (2 * SIN (ABS (na[jSeg]) / 2))
        sD = sChord / 2 * COS (na[jSeg] / 2) / SIN (na[jSeg] / 2)
        sCx = (nx[jSeg] + nx[jSeg + 1]) / 2 - sdy / sChord * sD
        sCy = (ny[jSeg] + ny[jSeg + 1]) / 2 + sdx / sChord * sD
        sLen = sRad * ABS (na[jSeg]) * PI / 180
    ENDIF
RETURN

"park":
    ! the insert grip's parked position: one third along segment jSeg
    IF sIsArc THEN
        pth = na[jSeg] / 3
        pvx = nx[jSeg] - sCx
        pvy = ny[jSeg] - sCy
        pkx = sCx + pvx * COS (pth) - pvy * SIN (pth)
        pky = sCy + pvx * SIN (pth) + pvy * COS (pth)
    ELSE
        pkx = nx[jSeg] + (nx[jSeg + 1] - nx[jSeg]) / 3
        pky = ny[jSeg] + (ny[jSeg + 1] - ny[jSeg]) / 3
    ENDIF
RETURN
```

- [ ] **Step 5: Write `gdl-src/swept-beam/scripts/3d.gdl`** (first increment: rectangle only)

```gdl
! Swept Beam -- 3D Script
! One TUBE{2} per section component along the sampled path (spec 1.4).
! TUBE keeps the section plumb (W up, U to the right looking along the path).

MODEL SOLID
IF nPath < 2 THEN END

sgnFlip = 1
IF flipProfile THEN sgnFlip = -1
nTube = nPath + 2
tubeMask = 1 + 2 + 16 + 32

! Flip mirrors the section, not the path: under MULX -1 the path is fed with
! x negated, so it lands where it belongs and only U flips.
IF flipProfile THEN MULX -1

! ---- rectangle section (also the fallback) ----
rw = MAX (0.001, rectW)
rh = MAX (0.001, rectH)
rSurf = 0
nRq = REQUEST{2} ("Building_Material_info", rectBMat, "gs_bmat_surface", rSurf)
IF overrideSurface THEN rSurf = surfaceOverride
BUILDING_MATERIAL rectBMat
PUT -rw / 2 - profileOffsetU,  rh / 2 - profileOffsetW, 0, rSurf
PUT -rw / 2 - profileOffsetU, -rh / 2 - profileOffsetW, 0, rSurf
PUT  rw / 2 - profileOffsetU, -rh / 2 - profileOffsetW, 0, rSurf
PUT  rw / 2 - profileOffsetU,  rh / 2 - profileOffsetW, 0, rSurf
GOSUB "putPath"
TUBE{2} rSurf, rSurf, rSurf, 4, nTube, tubeMask, GET (NSP)

IF flipProfile THEN DEL 1
END

"putPath":
    ! phantom start point along the true start tangent, the samples, phantom end
    PUT (pathX[1] - startTX) * sgnFlip, pathY[1] - startTY, pathZ[1] - startTZ, pathRoll[1] * sgnFlip
    FOR iP = 1 TO nPath
        PUT pathX[iP] * sgnFlip, pathY[iP], pathZ[iP], pathRoll[iP] * sgnFlip
    NEXT iP
    PUT (pathX[nPath] + endTX) * sgnFlip, pathY[nPath] + endTY, pathZ[nPath] + endTZ, pathRoll[nPath] * sgnFlip
RETURN
```

- [ ] **Step 6: Write `gdl-src/swept-beam/scripts/2d.gdl`** (first increment: projection, axis, heights, fixed node hotspots)

```gdl
! Swept Beam -- 2D Script
! Plan symbol: a projection of the 3D model (spec 1.5). parts = 1+2+4+8 keeps
! the 3D grips out of the plan. planMethod: 2 hidden lines, 3 shading (cut
! polygons exist only with 3); +32 draws with the current pen.

planMethod = 2 + 32

IF nPath < 2 THEN
    LINE2 -0.1, 0, 0.1, 0
    LINE2 0, -0.1, 0, 0.1
ELSE
    PEN outlinePen
    PROJECT2{3} 3, 270, planMethod, 1 + 2 + 4 + 8
ENDIF

IF showAxis AND nPath > 1 THEN
    PEN axisPen
    LINE_TYPE axisLineType
    FOR iP = 1 TO nPath - 1
        LINE2 pathX[iP], pathY[iP], pathX[iP + 1], pathY[iP + 1]
    NEXT iP
ENDIF

IF showNodeHeights THEN
    DEFINE STYLE "sbHeights" "Arial", MAX (0.5, textSizeMm), 1, 0
    SET STYLE "sbHeights"
    PEN textPen
    FOR iN = 1 TO nNode
        TEXT2 nodeX[iN], nodeY[iN], STR ("%.3m", GLOB_ELEVATION + nodeZ[iN])
    NEXT iN
ENDIF

! (Task 7 replaces these fixed hotspots with the editing grips.)
FOR iN = 1 TO nNode
    HOTSPOT2 nodeX[iN], nodeY[iN]
NEXT iN
```

- [ ] **Step 7: Write `gdl-src/swept-beam/scripts/ui.gdl`**

```gdl
! Swept Beam -- User Interface Script (three pages, spec 1.6)

UI_DIALOG "Swept Beam"
UI_CURRENT_PAGE gs_ui_current_page

UI_PAGE 1, -1, "Path"
UI_STYLE 2, 1
UI_OUTFIELD "Path", 12, 10, 420, 22
UI_STYLE 0, 0
UI_SEPARATOR 12, 34, 432, 34
UI_OUTFIELD "Slope (%)", 18, 50, 245, 18
UI_INFIELD "slopePercent", 278, 48, 146, 19
UI_BUTTON UI_FUNCTION, "Apply slope", 278, 74, 146, 22, 1
UI_OUTFIELD "Curve tolerance", 18, 110, 245, 18
UI_INFIELD "pathTolerance", 278, 108, 146, 19
UI_OUTFIELD "Nodes: " + STR (VARDIM1 (nodeX), 1, 0), 18, 140, 406, 18
UI_STYLE 1, 2
UI_OUTFIELD "Apply slope sets every node height from node 1 along the plan length.", 18, 166, 406, 20
UI_OUTFIELD "Exact node values and roll: parameter list, array editor.", 18, 188, 406, 20
UI_STYLE 0, 0
UI_BUTTON UI_NEXT, "Next >", 352, 244, 72, 20

UI_PAGE 2, -1, "Profile"
UI_STYLE 2, 1
UI_OUTFIELD "Profile", 12, 10, 420, 22
UI_STYLE 0, 0
UI_SEPARATOR 12, 34, 432, 34
UI_OUTFIELD "Section source", 18, 44, 245, 18
UI_INFIELD "profileMode", 278, 42, 146, 19
IF profileMode = "Profile attribute" THEN
    UI_OUTFIELD "Profile", 18, 68, 245, 18
    UI_INFIELD "beamProfile", 278, 66, 146, 19
ENDIF
UI_OUTFIELD "Rectangle width (also the fallback)", 18, 92, 245, 18
UI_INFIELD "rectW", 278, 90, 146, 19
UI_OUTFIELD "Rectangle height", 18, 116, 245, 18
UI_INFIELD "rectH", 278, 114, 146, 19
UI_OUTFIELD "Rectangle building material", 18, 140, 245, 18
UI_INFIELD "rectBMat", 278, 138, 146, 19
UI_OUTFIELD "Offset sideways / up", 18, 164, 245, 18
UI_INFIELD "profileOffsetU", 278, 162, 70, 19
UI_INFIELD "profileOffsetW", 354, 162, 70, 19
UI_OUTFIELD "Flip section", 18, 188, 245, 18
UI_INFIELD "flipProfile", 278, 186, 146, 19
UI_OUTFIELD "Override surfaces", 18, 212, 245, 18
UI_INFIELD "overrideSurface", 278, 210, 20, 19
IF overrideSurface THEN UI_INFIELD "surfaceOverride", 304, 210, 120, 19
UI_BUTTON UI_PREV, "< Back", 12, 244, 72, 20
UI_BUTTON UI_NEXT, "Next >", 352, 244, 72, 20

UI_PAGE 3, -1, "Floor plan"
UI_STYLE 2, 1
UI_OUTFIELD "Floor plan", 12, 10, 420, 22
UI_STYLE 0, 0
UI_SEPARATOR 12, 34, 432, 34
UI_OUTFIELD "Outline pen", 18, 50, 245, 18
UI_INFIELD "outlinePen", 278, 48, 146, 19
UI_OUTFIELD "Show reference axis", 18, 78, 245, 18
UI_INFIELD "showAxis", 278, 76, 146, 19
IF showAxis THEN
    UI_OUTFIELD "Axis pen / line type", 18, 102, 245, 18
    UI_INFIELD "axisPen", 278, 100, 70, 19
    UI_INFIELD "axisLineType", 354, 100, 70, 19
ENDIF
UI_OUTFIELD "Show node heights", 18, 134, 245, 18
UI_INFIELD "showNodeHeights", 278, 132, 146, 19
IF showNodeHeights THEN
    UI_OUTFIELD "Text pen / size (mm)", 18, 158, 245, 18
    UI_INFIELD "textPen", 278, 156, 70, 19
    UI_INFIELD "textSizeMm", 354, 156, 70, 19
ENDIF
UI_BUTTON UI_PREV, "< Back", 12, 244, 72, 20
```

- [ ] **Step 8: Build and check the scripts offline**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out build/swept-beam`
Expected output includes `built with the Archicad 27 converter`, `validation (Archicad 27): scripts interpret cleanly` and the same for 29. If the interpreter reports errors, fix the named script line and rebuild; do not continue with findings open.

- [ ] **Step 9: Commit**

```bash
git add gdl-src/swept-beam
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: Swept Beam library part, first increment

Path sampling with plan arcs and linear rise, a rectangle swept with TUBE{2}
and its building material, PROJECT2{3} plan with axis and node heights,
Apply slope, and the three-page dialog.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Gate A (live): sweep, plan, section, API writes, Archicad 27

No code unless a gate step fails. Every step records its result in `docs/superpowers/specs/2026-09-29-swept-beam-findings.md` (create it with a `# Swept Beam: gate findings` heading and one `## Gate A` section: one line per step, the observed result, and the decision it implies).

**Before starting:** ask the user for the linked GDL workspace folder path (the folder added in Library Manager in both test files), call it `$WS` below. Call `list_instances`; note the ports whose `project_name` is `MCP-Test` (Archicad 29) and `MCP-Test_27` (Archicad 27). If the 27 instance reports `code 4001`, ask the user to close the open dialog.

- [ ] **Step 1: Build into the workspace**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out "$WS"`
Expected: `Swept Beam.gsm` in `$WS`, clean validation for 27 and 29.

- [ ] **Step 2: Place and render in Archicad 29**

MCP call: `deploy_gdl_object(name="Swept Beam", x=0, y=-5, keep=true, port=<MCP-Test port>)`.
Expected: the render shows a straight 3 m beam with a 0.2 x 0.2 square section. Record the element GUID as `$G`.

- [ ] **Step 3: Write a helix through the API (array writes, attribute index, Parameter Script after an API write)**

MCP call `execute_write_api_command` with `SetGDLParametersOfElements`:

```json
{"elementsWithGDLParameters": [{"elementId": {"guid": "$G"}, "gdlParameters": [
  {"name": "nodeX", "value": [0.0, 3.0, 3.0]},
  {"name": "nodeY", "value": [0.0, 0.0, 3.0]},
  {"name": "nodeZ", "value": [0.0, 0.5, 1.5]},
  {"name": "nodeRoll", "value": [0.0, 0.0, 30.0]},
  {"name": "segArc", "value": [0.0, 90.0, 0.0]}]}]}
```

Then read the parameters back with `execute_read_api_command` `GetGDLParametersOfElements` for `$G`, and render the element with `execute_read_api_command` `GetElementPreviewImage` (`{"elementId": {"guid": "$G"}, "imageType": "3D", "format": "png", "width": 700, "height": 700}`).
Expected: arrays read back as written; the render shows a straight 3 m piece heading +x, then a 45 degree mitre at node 2, then a quarter helix turning left (radius 2.121 m, centre (1.5, 1.5)) that rises 1.0 m to node 3, with the section rolling to 30 degrees at the end. Record whether `segSag`, `insX`, `insY` now have 3 items with the parked values (`insX[1] = 1.0`; `segSag[2] = 3 / 2 * tan(22.5 deg) = 0.6213`). If they still have 2 items, the Parameter Script does not run after an API write: record it (the MCP tool writes the helpers itself anyway).

- [ ] **Step 4: Whole-array PARAMETERS**

In Archicad, open the beam's settings and press "Apply slope" with `slopePercent = 10`. Read `nodeZ` back with `GetGDLParametersOfElements`.
Expected: `nodeZ = [0, 0.3, 0.6332]`: the straight segment is 3 m of plan length, the quarter arc is 2.1213 * PI / 2 = 3.3322 m, so node 3 sits at 0.1 * 6.3322. If `nodeZ` did not change, whole-array `PARAMETERS` does not work: STOP and report to the user, because inserting and merging nodes depend on it.

- [ ] **Step 5: Absolute z on placement**

MCP call `execute_write_api_command` `CreateObjects` with `{"objectsData": [{"libraryPartName": "Swept Beam", "coordinates": {"x": 0, "y": -8, "z": 1.0}, "floorIndex": 0}]}`, then `GetDetailsOfElements` on the new element.
Expected: `details.origin.z == 1.0` and `floorIndex == 0`. Record whether z is absolute (it matches story 0's elevation plus 1.0). Delete this element afterwards with `delete_elements(confirm=true)`.

- [ ] **Step 6: Plan symbol and cut**

Ask the user to look at `$G` on the floor plan and in a section across the helix.
Expected: plan shows the projected outline in `outlinePen`; with `showAxis` on, the dashed reference axis follows the path centre; with `showNodeHeights` on, each node shows its height; the section shows the cut with the rectangle's building material fill. Then rebuild once with `planMethod = 3 + 32` in `2d.gdl`, reload (`execute_write_api_command ReloadLibraries`), and ask whether a cut fill appears where the plan cut plane crosses the beam (set a node height above the cut plane first). Keep the method that gives the cut fill if there is one; record the choice and leave `2d.gdl` on it.

- [ ] **Step 7: Archicad 27**

MCP call: `deploy_gdl_object(name="Swept Beam", x=0, y=-5, keep=true, port=<MCP-Test_27 port>)`.
Expected: the same straight beam renders in Archicad 27. Record the Archicad 27 build number.

- [ ] **Step 8: Commit the findings**

```bash
git add docs/superpowers/specs/2026-09-29-swept-beam-findings.md gdl-src/swept-beam/scripts/2d.gdl
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "docs: Swept Beam gate A findings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Profile attribute sweep, then Gate B

**Files:**
- Modify: `gdl-src/swept-beam/scripts/3d.gdl`

**Interfaces:**
- Consumes: Master Script path variables (Task 4), subroutine `"putPath"` (Task 4).
- Produces: `profileMode = "Profile attribute"` sweeps `beamProfile` live; falls back to the rectangle when the profile cannot be read.

- [ ] **Step 1: Replace the rectangle block in `3d.gdl`** (from `! ---- rectangle section` down to the rectangle's `TUBE{2}` line) with:

```gdl
! ---- section from a Profile Manager profile, read live (closet pattern) ----
useProfile = 0
nComp = 0
DIM compTypes[], geo[], compStart[], compEnd[], eSurf[]
IF profileMode = "Profile attribute" THEN
    rq1 = REQUEST ("Profile_components", beamProfile, nComp, compTypes)
    rq2 = REQUEST ("Profile_default_geometry", beamProfile, geo)
    IF rq1 > 0 AND rq2 > 0 AND nComp > 0 THEN
        rawCount = VARDIM1 (geo)
        okSplit = 1
        nextIdx = nComp + 1
        FOR iC = 1 TO nComp
            nNodesC = INT (geo[iC])
            compStart[iC] = nextIdx
            compEnd[iC] = nextIdx + nNodesC * 5 - 1
            IF nNodesC < 3 THEN okSplit = 0
            IF compEnd[iC] > rawCount THEN okSplit = 0
            nextIdx = compEnd[iC] + 1
        NEXT iC
        IF nextIdx - 1 <> rawCount THEN okSplit = 0
        useProfile = okSplit
    ENDIF
ENDIF

IF useProfile THEN
    FOR iC = 1 TO nComp
        cBMat = 0
        rq = REQUEST{4} ("Profile_component_info", beamProfile, iC, "gs_profile_bmat", cBMat)
        cSurf = 0
        rq = REQUEST{4} ("Profile_component_info", beamProfile, iC, "gs_profile_surface", cSurf)
        nES = REQUEST{4} ("Profile_component_info", beamProfile, iC, "gs_profile_comp_surfaces", eSurf)
        IF overrideSurface THEN cSurf = surfaceOverride
        BUILDING_MATERIAL cBMat
        ! a single outline drops its closing duplicate; with holes TUBE{2} needs the -1 nodes
        nEnds = 0
        FOR kG = compStart[iC] TO compEnd[iC] STEP 5
            IF geo[kG + 4] = -1 THEN nEnds = nEnds + 1
        NEXT kG
        nSec = 0
        iNodeC = 0
        FOR kG = compStart[iC] TO compEnd[iC] STEP 5
            iNodeC = iNodeC + 1
            stG = geo[kG + 4]
            skipIt = 0
            IF stG = -1 AND nEnds = 1 AND kG = compEnd[iC] - 4 THEN skipIt = 1
            IF skipIt = 0 THEN
                tubeSt = stG
                IF stG >= 0 AND geo[kG + 3] = 0 THEN tubeSt = stG + 1
                edgeMat = cSurf
                IF overrideSurface = 0 AND iNodeC <= nES THEN edgeMat = eSurf[iNodeC]
                PUT geo[kG] - profileOffsetU, geo[kG + 1] - profileOffsetW, tubeSt, edgeMat
                nSec = nSec + 1
            ENDIF
        NEXT kG
        GOSUB "putPath"
        TUBE{2} cSurf, cSurf, cSurf, nSec, nTube, tubeMask, GET (NSP)
    NEXT iC
ELSE
    ! ---- rectangle section (also the fallback) ----
    rw = MAX (0.001, rectW)
    rh = MAX (0.001, rectH)
    rSurf = 0
    nRq = REQUEST{2} ("Building_Material_info", rectBMat, "gs_bmat_surface", rSurf)
    IF overrideSurface THEN rSurf = surfaceOverride
    BUILDING_MATERIAL rectBMat
    PUT -rw / 2 - profileOffsetU,  rh / 2 - profileOffsetW, 0, rSurf
    PUT -rw / 2 - profileOffsetU, -rh / 2 - profileOffsetW, 0, rSurf
    PUT  rw / 2 - profileOffsetU, -rh / 2 - profileOffsetW, 0, rSurf
    PUT  rw / 2 - profileOffsetU,  rh / 2 - profileOffsetW, 0, rSurf
    GOSUB "putPath"
    TUBE{2} rSurf, rSurf, rSurf, 4, nTube, tubeMask, GET (NSP)
ENDIF
```

- [ ] **Step 2: Build offline**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out build/swept-beam`
Expected: clean validation for 27 and 29.

- [ ] **Step 3: Gate B (live).** Build into `$WS`, `ReloadLibraries` on both ports. Ask the user for the exact names of the two test profiles they made in Profile Manager (solid square and hollow). Find their indices with `execute_read_api_command GetAttributesByType {"attributeType": "Profile"}`. On `$G` (the helix from gate A) set `profileMode = "Profile attribute"` and `beamProfile = <index>` with `SetGDLParametersOfElements`, render after each. Record in `## Gate B`:
  1. Solid square profile: same shape as the rectangle mode.
  2. Hollow profile: the render and a section show the hole and each component's building material. If the hole is missing or the tube is broken, record it and test the closing-node rule in the `nEnds` block.
  3. A profile with curved edges (ask the user to add a round tube or rounded-corner profile): smooth sides, no facet lines along the beam.
  4. Orientation: the user places a native Beam along x with the same profile and compares the section with a straight Swept Beam along x. If mirrored, set the `flipProfile` default to `true` in `params.toml`.
  5. Missing profile: set `beamProfile` to an index that does not exist (for example 9999): the rectangle appears, nothing breaks.
  6. Archicad 27: repeat 1 and 2 on the 27 port.

- [ ] **Step 4: Commit**

```bash
git add gdl-src/swept-beam docs/superpowers/specs/2026-09-29-swept-beam-findings.md
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: sweep Profile Manager profiles live, gate B findings

Each profile component becomes one TUBE{2} with its building material and
edge surfaces; holes use the profile's own contour ends. A profile that
cannot be read falls back to the rectangle.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Editing grips and Parameter Script reactions, then Gate C

**Files:**
- Modify: `gdl-src/swept-beam/scripts/2d.gdl`, `gdl-src/swept-beam/scripts/3d.gdl`, `gdl-src/swept-beam/scripts/param.gdl`

**Interfaces:**
- Consumes: helper arrays `segSag insX insY`, subroutines `"seg"` and `"park"` (Task 4).
- Produces: hotspot id ranges: plan node grips `100000 + 10 * i + k`, arc grips `200000 + 10 * j + k`, insert grips `300000 + 10 * j + k`, 3D node grips `400000 + 10 * i + k`.

- [ ] **Step 1: Replace the fixed hotspots at the end of `2d.gdl`** (from `! (Task 7 replaces` to the end) with:

```gdl
! ---- node grips: combined nodeX / nodeY drag (reference, Example 2) ----
FOR iN = 1 TO nNode
    hId = 100000 + iN * 10
    HOTSPOT2 nodeX[iN], 0, hId, nodeY[iN], 1 + 128
    HOTSPOT2 nodeX[iN], -1, hId + 1, nodeY[iN], 3
    HOTSPOT2 nodeX[iN], nodeY[iN], hId + 2, nodeY[iN], 2
    HOTSPOT2 0, nodeY[iN], hId + 3, nodeX[iN], 1 + 128
    HOTSPOT2 -1, nodeY[iN], hId + 4, nodeX[iN], 3
    HOTSPOT2 nodeX[iN], nodeY[iN], hId + 5, nodeX[iN], 2
NEXT iN

! ---- arc grips (segSag, sideways at the chord midpoint) and insert grips ----
FOR jS = 1 TO nNode - 1
    gdx = nodeX[jS + 1] - nodeX[jS]
    gdy = nodeY[jS + 1] - nodeY[jS]
    gChord = SQR (gdx * gdx + gdy * gdy)
    IF gChord > EPS AND jS <= VARDIM1 (segSag) THEN
        gmx = (nodeX[jS] + nodeX[jS + 1]) / 2
        gmy = (nodeY[jS] + nodeY[jS + 1]) / 2
        rnx = gdy / gChord
        rny = -gdx / gChord
        hId = 200000 + jS * 10
        HOTSPOT2 gmx, gmy, hId, segSag[jS], 1 + 128
        HOTSPOT2 gmx - rnx, gmy - rny, hId + 1, segSag[jS], 3
        HOTSPOT2 gmx + rnx * segSag[jS], gmy + rny * segSag[jS], hId + 2, segSag[jS], 2
    ENDIF
    IF jS <= VARDIM1 (insX) AND jS <= VARDIM1 (insY) THEN
        hId = 300000 + jS * 10
        HOTSPOT2 insX[jS], 0, hId, insY[jS], 1 + 128
        HOTSPOT2 insX[jS], -1, hId + 1, insY[jS], 3
        HOTSPOT2 insX[jS], insY[jS], hId + 2, insY[jS], 2
        HOTSPOT2 0, insY[jS], hId + 3, insX[jS], 1 + 128
        HOTSPOT2 -1, insY[jS], hId + 4, insX[jS], 3
        HOTSPOT2 insX[jS], insY[jS], hId + 5, insX[jS], 2
    ENDIF
NEXT jS
```

- [ ] **Step 2: Add the 3D grips to `3d.gdl`** between `IF flipProfile THEN DEL 1` and `END` (after the mirror is removed, so grips sit on the real nodes):

```gdl
! ---- 3D node grips: combined nodeX / nodeY / nodeZ drag ----
FOR iN = 1 TO nNode
    hId = 400000 + iN * 10
    HOTSPOT 0, nodeY[iN], nodeZ[iN], hId, nodeX[iN], 1 + 128
    HOTSPOT -1, nodeY[iN], nodeZ[iN], hId + 1, nodeX[iN], 3
    HOTSPOT nodeX[iN], nodeY[iN], nodeZ[iN], hId + 2, nodeX[iN], 2
    HOTSPOT nodeX[iN], 0, nodeZ[iN], hId + 3, nodeY[iN], 1 + 128
    HOTSPOT nodeX[iN], -1, nodeZ[iN], hId + 4, nodeY[iN], 3
    HOTSPOT nodeX[iN], nodeY[iN], nodeZ[iN], hId + 5, nodeY[iN], 2
    HOTSPOT nodeX[iN], nodeY[iN], 0, hId + 6, nodeZ[iN], 1 + 128
    HOTSPOT nodeX[iN], nodeY[iN], -1, hId + 7, nodeZ[iN], 3
    HOTSPOT nodeX[iN], nodeY[iN], nodeZ[iN], hId + 8, nodeZ[iN], 2
NEXT iN
```

- [ ] **Step 3: Add the reactions to `param.gdl`**, replacing the line `! (Task 7 adds the arc grip, insert and merge reactions here.)`:

```gdl
! arc grip: a moved segSag item becomes that segment's arc angle
IF GLOB_MODPAR_NAME = "segSag" THEN
    FOR j = 1 TO n - 1
        IF j <= VARDIM1 (segSag) THEN
            jSeg = j
            GOSUB "seg"
            IF sChord > EPS THEN
                sOld = 0
                IF sIsArc THEN sOld = sChord / 2 * TAN (na[j] / 4)
                IF ABS (segSag[j] - sOld) > 0.0005 THEN na[j] = 4 * ATN (2 * segSag[j] / sChord)
            ENDIF
        ENDIF
    NEXT j
ENDIF

! insert grip: the item that left its parked point becomes a new node there
IF GLOB_MODPAR_NAME = "insX" OR GLOB_MODPAR_NAME = "insY" THEN
    jIns = 0
    FOR j = 1 TO n - 1
        IF jIns = 0 AND j <= VARDIM1 (insX) AND j <= VARDIM1 (insY) THEN
            jSeg = j
            GOSUB "seg"
            GOSUB "park"
            IF ABS (insX[j] - pkx) + ABS (insY[j] - pky) > 0.001 THEN jIns = j
        ENDIF
    NEXT j
    IF jIns > 0 THEN
        newX = insX[jIns]
        newY = insY[jIns]
        newZ = nz[jIns] + (nz[jIns + 1] - nz[jIns]) / 3
        newR = nr[jIns] + (nr[jIns + 1] - nr[jIns]) / 3
        jSeg = jIns
        GOSUB "seg"
        a1 = 0
        a2 = 0
        IF sIsArc THEN
            c1 = SQR ((newX - nx[jIns])^2 + (newY - ny[jIns])^2)
            c2 = SQR ((nx[jIns + 1] - newX)^2 + (ny[jIns + 1] - newY)^2)
            IF c1 <= 2 * sRad THEN a1 = SGN (na[jIns]) * 2 * ASN (c1 / (2 * sRad))
            IF c2 <= 2 * sRad THEN a2 = SGN (na[jIns]) * 2 * ASN (c2 / (2 * sRad))
        ENDIF
        FOR k = n TO jIns + 1 STEP -1
            nx[k + 1] = nx[k]
            ny[k + 1] = ny[k]
            nz[k + 1] = nz[k]
            nr[k + 1] = nr[k]
            na[k + 1] = na[k]
        NEXT k
        nx[jIns + 1] = newX
        ny[jIns + 1] = newY
        nz[jIns + 1] = newZ
        nr[jIns + 1] = newR
        na[jIns] = a1
        na[jIns + 1] = a2
        n = n + 1
        na[n] = 0
    ENDIF
ENDIF

! merge: a node dragged within 1 cm (plan) of a neighbour disappears
IF (GLOB_MODPAR_NAME = "nodeX" OR GLOB_MODPAR_NAME = "nodeY" OR GLOB_MODPAR_NAME = "nodeZ") AND n > 2 THEN
    j = 1
    WHILE j < n AND n > 2 DO
        dPlan = SQR ((nx[j + 1] - nx[j])^2 + (ny[j + 1] - ny[j])^2)
        IF dPlan < 0.01 THEN
            jDel = j + 1
            IF jDel = n THEN jDel = j
            IF jDel > 1 AND jDel < n THEN
                lenA = SQR ((nx[jDel] - nx[jDel - 1])^2 + (ny[jDel] - ny[jDel - 1])^2)
                lenB = SQR ((nx[jDel + 1] - nx[jDel])^2 + (ny[jDel + 1] - ny[jDel])^2)
                IF lenB > lenA THEN na[jDel - 1] = na[jDel]
            ENDIF
            FOR k = jDel TO n - 1
                nx[k] = nx[k + 1]
                ny[k] = ny[k + 1]
                nz[k] = nz[k + 1]
                nr[k] = nr[k + 1]
                na[k] = na[k + 1]
            NEXT k
            n = n - 1
            na[n] = 0
        ELSE
            j = j + 1
        ENDIF
    ENDWHILE
ENDIF
```

The `A` reaction in step 2 of the script already runs before these; it only acts on two-node straight beams, which the merge and insert blocks leave consistent because the parking step and the write-back run after them.

- [ ] **Step 4: Build offline**

Run: `uv run archicad-gdl build-source gdl-src/swept-beam --out build/swept-beam`
Expected: clean validation for 27 and 29.

- [ ] **Step 5: Gate C (live).** Build into `$WS`, `ReloadLibraries` on both ports. Record in `## Gate C`:
  1. Plan node drag on `$G`: the node follows the cursor freely in plan (combined drag), the beam redraws.
  2. 3D drag: create a 60-node helix with `SetGDLParametersOfElements` on a new placed beam (`nodeX[i] = 4 * cos(6 * i deg)`, `nodeY[i] = 4 * sin(6 * i deg)`, `nodeZ[i] = 0.05 * i`, `segArc` all 0, 60 items each). Ask the user to drag a node up in 3D and report whether it feels responsive.
  3. Arc grip: drag the midpoint grip of a straight segment sideways: it becomes an arc through the grip.
  4. Insert grip: drag the one-third grip of a segment away: a new node appears at the drop point; node count grows by one; heights stay continuous.
  5. Merge: drag a node onto its neighbour: node count drops by one.
  6. Apply slope on a 3-node beam with an arc: heights follow the plan length.
  7. Placement: ask the user to place a new Swept Beam from the Object tool with each placement method (single, rotated, diagonal, rotated diagonal) and report which gives start and end in two clicks. Record it; `docs/swept-beam.md` (Task 10) names that method.
  8. Array editor (Review Focus 5): delete the last item of `nodeY` only in the parameter list's array editor: the beam still draws and the arrays come back with equal length.
  9. Archicad 27: repeat 1, 4 and 6 on the 27 port.
  If step 1, 3 or 4 fails because array items cannot be hotspot targets, STOP and report to the user.

- [ ] **Step 6: Commit**

```bash
git add gdl-src/swept-beam docs/superpowers/specs/2026-09-29-swept-beam-findings.md
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: Swept Beam grips for moving, curving, inserting and merging nodes

Plan and 3D node drags use combined hotspots; arc and insert grips write
helper arrays that the Parameter Script turns into arcs and new nodes.
Gate C findings recorded.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Path geometry for the MCP tool

**Files:**
- Create: `src/archicad_mcp/core/swept_path.py`
- Test: `tests/test_swept_path.py`

**Interfaces:**
- Produces: `PathError(ValueError)`; `Point = tuple[float, float, float]`; `DUPLICATE_EPS = 0.001`; `ARC_EPS_DEG = 0.01`; `SweptPath(nodes: list[Point], arcs: list[float], max_deviation: float = 0.0)` with `.arc_count`, `.straight_count`, `.segment_lengths() -> list[float]`, `.steepest_slope_deg() -> float`; `arc_geometry(p1, p2, arc_deg) -> (cx, cy, radius)`; `segment_plan_length(p1, p2, arc_deg) -> float`; `chain_wire_edges(edges: list[tuple[int, int]]) -> list[int]`; `dedupe(points) -> list[Point]`; `fit_points(points, tol) -> SweptPath`; `apply_slope(nodes, arcs, start_z, slope_percent) -> list[Point]`; `grip_helpers(nodes, arcs) -> tuple[list[float], list[float], list[float]]`.

- [ ] **Step 1: Write the failing tests** (`tests/test_swept_path.py`)

```python
"""Geometry behind create_swept_beam."""

import json
import math
from pathlib import Path

import pytest

from archicad_mcp.core import swept_path as sp

FIXTURE = Path(__file__).parent / "fixtures" / "swept_beam" / "morph_line_mcp_test.json"


def _helix(radius, sweep_deg, rise, n, ccw=True):
    sign = 1 if ccw else -1
    return [(radius * math.cos(math.radians(sweep_deg * k / (n - 1)) * sign),
             radius * math.sin(math.radians(sweep_deg * k / (n - 1)) * sign),
             rise * k / (n - 1)) for k in range(n)]


def test_arc_geometry_quarter_turn_left():
    cx, cy, r = sp.arc_geometry((0, 0, 0), (2, 2, 0), 90.0)
    assert (round(cx, 9), round(cy, 9), round(r, 9)) == (0, 2, 2)


def test_major_arc_centre_is_on_the_right():
    cx, cy, r = sp.arc_geometry((0, 0, 0), (2, 0, 0), 270.0)
    assert round(cx, 9) == 1 and cy < 0 and round(r, 6) == round(1 / math.sin(math.radians(135)), 6)


def test_helix_fits_one_arc_left():
    path = sp.fit_points(_helix(5.0, 90.0, 1.0, 46), 0.002)
    assert len(path.nodes) == 2
    assert path.arcs[0] == pytest.approx(90.0, abs=1e-6) and path.arcs[1] == 0.0
    assert path.nodes[-1] == pytest.approx((0.0, 5.0, 1.0))
    assert path.max_deviation < 1e-6


def test_helix_fits_one_arc_right():
    path = sp.fit_points(_helix(5.0, 90.0, 1.0, 46, ccw=False), 0.002)
    assert path.arcs[0] == pytest.approx(-90.0, abs=1e-6)


def test_straight_rise_is_one_segment():
    pts = [(k * 0.5, 0.0, k * 0.05) for k in range(21)]
    path = sp.fit_points(pts, 0.002)
    assert len(path.nodes) == 2 and path.arcs == [0.0, 0.0]


def test_bent_rise_splits():
    pts = [(k * 0.5, 0.0, min(k, 20 - k) * 0.1) for k in range(21)]
    assert len(sp.fit_points(pts, 0.002).nodes) >= 3


def test_duplicates_are_dropped():
    pts = [(0, 0, 0), (0, 0, 0), (1, 0, 0), (1, 0.0000001, 0), (2, 0, 0)]
    assert sp.dedupe(pts) == [(0, 0, 0), (1, 0, 0), (2, 0, 0)]


def test_fewer_than_two_distinct_points():
    with pytest.raises(sp.PathError, match="two distinct points"):
        sp.fit_points([(1, 1, 1), (1, 1, 1)], 0.002)


def test_chain_orders_from_an_end():
    assert sp.chain_wire_edges([(2, 1), (0, 1), (3, 2)]) in ([0, 1, 2, 3], [3, 2, 1, 0])


def test_chain_refuses_branch_loop_and_pieces():
    with pytest.raises(sp.PathError, match="branches"):
        sp.chain_wire_edges([(0, 1), (1, 2), (1, 3)])
    with pytest.raises(sp.PathError, match="closed loop"):
        sp.chain_wire_edges([(0, 1), (1, 2), (2, 0)])
    with pytest.raises(sp.PathError, match="separate piece"):
        sp.chain_wire_edges([(0, 1), (2, 3)])


def test_mcp_test_morph_line_fits_within_2mm():
    raw = json.loads(FIXTURE.read_text())
    edges = [tuple(e["vertexIds"]) for e in raw["wireEdges"]]
    order = sp.chain_wire_edges(edges)
    o = raw["origin"]
    pts = [(raw["vertices"][i]["x"] + o["x"], raw["vertices"][i]["y"] + o["y"],
            raw["vertices"][i]["z"] + o["z"]) for i in order]
    path = sp.fit_points(pts, 0.002)
    assert path.max_deviation <= 0.002
    assert path.nodes[0] == pytest.approx(pts[0]) and path.nodes[-1] == pytest.approx(pts[-1])
    assert len(path.arcs) == len(path.nodes) and path.arcs[-1] == 0.0


def test_apply_slope_along_plan_length():
    nodes = [(0, 0, 9), (2, 2, 9), (2, 12, 9)]
    out = sp.apply_slope(nodes, [90.0, 0.0, 0.0], 1.0, 10.0)
    assert out[0][2] == 1.0
    assert out[1][2] == pytest.approx(1.0 + 0.1 * math.pi)
    assert out[2][2] == pytest.approx(1.0 + 0.1 * (math.pi + 10))


def test_grip_helpers_match_the_parameter_script():
    sag, ix, iy = sp.grip_helpers([(0, 0, 0), (2, 2, 0), (5, 2, 0)], [90.0, 0.0, 0.0])
    assert sag[0] == pytest.approx(math.sqrt(8) / 2 * math.tan(math.radians(22.5)))
    assert (ix[0], iy[0]) == pytest.approx((1.0, 2 - math.sqrt(3)))
    assert (ix[1], iy[1], sag[1]) == pytest.approx((3.0, 2.0, 0.0))
    assert (ix[2], iy[2], sag[2]) == pytest.approx((5.0, 2.0, 0.0))


def test_steepest_slope():
    path = sp.SweptPath([(0, 0, 0), (1, 0, 1)], [0.0, 0.0])
    assert path.steepest_slope_deg() == pytest.approx(45.0)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_swept_path.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement** (`src/archicad_mcp/core/swept_path.py`)

```python
"""Geometry for create_swept_beam: a source curve becomes Swept Beam nodes.

A Swept Beam segment is straight or a plan arc, and its height changes
linearly along the plan length (spec 1.1). Sources arrive as ordered 3D
points (a morph line's wire edges, points from a DWG or Rhino curve) or as a
polyline's own segments. Points are fitted greedily: a piece grows while every
point in it stays within the tolerance of one line or one plan circle and of
one straight rise, the method that fitted the Stanezice rails to 2.1 mm.

Arc angles are degrees and positive when the path turns left
(counter-clockwise), the Swept Beam's segArc convention.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float, float]
DUPLICATE_EPS = 0.001
ARC_EPS_DEG = 0.01  # the GDL treats smaller arc angles as straight


class PathError(ValueError):
    """The source cannot become one Swept Beam path."""


def arc_geometry(p1, p2, arc_deg: float) -> tuple[float, float, float]:
    """Centre x, y and radius of a segment's plan arc."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    half = math.radians(arc_deg) / 2
    radius = chord / (2 * abs(math.sin(half)))
    d = chord / 2 * math.cos(half) / math.sin(half)
    return ((p1[0] + p2[0]) / 2 - dy / chord * d,
            (p1[1] + p2[1]) / 2 + dx / chord * d,
            radius)


def segment_plan_length(p1, p2, arc_deg: float) -> float:
    chord = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if abs(arc_deg) <= ARC_EPS_DEG or chord < 1e-12:
        return chord
    return arc_geometry(p1, p2, arc_deg)[2] * abs(math.radians(arc_deg))


@dataclass
class SweptPath:
    nodes: list[Point]
    arcs: list[float]
    max_deviation: float = 0.0

    @property
    def arc_count(self) -> int:
        return sum(1 for a in self.arcs[:-1] if abs(a) > ARC_EPS_DEG)

    @property
    def straight_count(self) -> int:
        return len(self.nodes) - 1 - self.arc_count

    def segment_lengths(self) -> list[float]:
        return [segment_plan_length(self.nodes[i], self.nodes[i + 1], self.arcs[i])
                for i in range(len(self.nodes) - 1)]

    def steepest_slope_deg(self) -> float:
        worst = 0.0
        for i, length in enumerate(self.segment_lengths()):
            rise = abs(self.nodes[i + 1][2] - self.nodes[i][2])
            angle = 90.0 if length < 1e-9 else math.degrees(math.atan2(rise, length))
            worst = max(worst, angle)
        return worst


def chain_wire_edges(edges: list[tuple[int, int]]) -> list[int]:
    """Vertex order of one open, unbranched line of edges."""
    adj: dict[int, list[int]] = {}
    for a, b in edges:
        if a != b:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
    if not adj:
        raise PathError("The morph has no wire edges. Draw the guide as a morph line.")
    branch = sorted(v for v, near in adj.items() if len(near) > 2)
    if branch:
        raise PathError(f"The morph line branches at vertex {branch[0]}. "
                        "A Swept Beam follows one unbranched line.")
    ends = sorted(v for v, near in adj.items() if len(near) == 1)
    if not ends:
        raise PathError("The morph line is a closed loop. A Swept Beam needs a start and an end.")
    order, prev = [ends[0]], None
    while True:
        nxt = [v for v in adj[order[-1]] if v != prev]
        if not nxt:
            break
        prev = order[-1]
        order.append(nxt[0])
    if len(order) != len(adj):
        raise PathError("The morph line has more than one separate piece. "
                        "Use a morph with one continuous line.")
    return order


def dedupe(points) -> list[Point]:
    out: list[Point] = []
    for p in points:
        q = (float(p[0]), float(p[1]), float(p[2]))
        if not out or math.dist(out[-1], q) >= DUPLICATE_EPS:
            out.append(q)
    return out


def _rise_deviation(pts: list[Point], s_vals: list[float]) -> list[float]:
    total = s_vals[-1]
    z0, z1 = pts[0][2], pts[-1][2]
    return [abs(p[2] - (z0 + (z1 - z0) * (s / total if total > 1e-12 else 0.0)))
            for p, s in zip(pts, s_vals)]


def _fit_line(pts: list[Point], tol: float) -> tuple[float, float] | None:
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return None
    ux, uy = dx / chord, dy / chord
    s_vals, off = [], []
    for p in pts:
        px, py = p[0] - a[0], p[1] - a[1]
        s_vals.append(px * ux + py * uy)
        off.append(abs(px * uy - py * ux))
    if max(off) > tol or any(s2 < s1 - tol for s1, s2 in zip(s_vals, s_vals[1:])):
        return None
    dz = _rise_deviation(pts, s_vals)
    if max(dz) > tol:
        return None
    return 0.0, max(math.hypot(o, z) for o, z in zip(off, dz))


def _circle(a, m, b) -> tuple[float, float, float] | None:
    ax, ay, bx, by, cx, cy = a[0], a[1], m[0], m[1], b[0], b[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _fit_arc(pts: list[Point], tol: float) -> tuple[float, float] | None:
    a, m, b = pts[0], pts[len(pts) // 2], pts[-1]
    circle = _circle(a, m, b)
    if circle is None:
        return None
    cx, cy, r = circle
    ccw = (m[0] - a[0]) * (b[1] - m[1]) - (m[1] - a[1]) * (b[0] - m[0]) > 0
    start = math.atan2(a[1] - cy, a[0] - cx)
    progress, radial = [], []
    for p in pts:
        d = (math.atan2(p[1] - cy, p[0] - cx) - start) % (2 * math.pi)
        if not ccw and d > 0:
            d -= 2 * math.pi
        progress.append(d)
        radial.append(abs(math.hypot(p[0] - cx, p[1] - cy) - r))
    if max(radial) > tol:
        return None
    steps = [q - p for p, q in zip(progress, progress[1:])]
    if (ccw and any(s < -1e-9 for s in steps)) or (not ccw and any(s > 1e-9 for s in steps)):
        return None
    dz = _rise_deviation(pts, [r * abs(t) for t in progress])
    if max(dz) > tol:
        return None
    return math.degrees(progress[-1]), max(math.hypot(o, z) for o, z in zip(radial, dz))


def fit_points(points, tol: float) -> SweptPath:
    pts = dedupe(points)
    if len(pts) < 2:
        raise PathError("The source has fewer than two distinct points.")
    nodes, arcs, worst = [pts[0]], [], 0.0
    s, last = 0, len(pts) - 1
    while s < last:
        end, arc, dev = s + 1, 0.0, 0.0
        e = s + 2
        while e <= last:
            piece = pts[s:e + 1]
            fit = _fit_line(piece, tol) or _fit_arc(piece, tol)
            if fit is None:
                break
            end, (arc, dev) = e, fit
            e += 1
        nodes.append(pts[end])
        arcs.append(arc)
        worst = max(worst, dev)
        s = end
    arcs.append(0.0)
    return SweptPath(nodes, arcs, worst)


def apply_slope(nodes, arcs, start_z: float, slope_percent: float) -> list[Point]:
    out = [(nodes[0][0], nodes[0][1], start_z)]
    acc = 0.0
    for i in range(1, len(nodes)):
        acc += segment_plan_length(nodes[i - 1], nodes[i], arcs[i - 1])
        out.append((nodes[i][0], nodes[i][1], start_z + slope_percent / 100 * acc))
    return out


def grip_helpers(nodes, arcs) -> tuple[list[float], list[float], list[float]]:
    """segSag, insX, insY exactly as the Parameter Script parks them (spec 1.3)."""
    sag, ins_x, ins_y = [], [], []
    for i, p1 in enumerate(nodes):
        if i == len(nodes) - 1:
            sag.append(0.0)
            ins_x.append(p1[0])
            ins_y.append(p1[1])
            continue
        p2, a = nodes[i + 1], arcs[i]
        chord = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if abs(a) <= ARC_EPS_DEG or chord < 1e-12:
            sag.append(0.0)
            ins_x.append(p1[0] + (p2[0] - p1[0]) / 3)
            ins_y.append(p1[1] + (p2[1] - p1[1]) / 3)
            continue
        cx, cy, _r = arc_geometry(p1, p2, a)
        sag.append(chord / 2 * math.tan(math.radians(a) / 4))
        th = math.radians(a) / 3
        vx, vy = p1[0] - cx, p1[1] - cy
        ins_x.append(cx + vx * math.cos(th) - vy * math.sin(th))
        ins_y.append(cy + vx * math.sin(th) + vy * math.cos(th))
    return sag, ins_x, ins_y
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_swept_path.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/swept_path.py tests/test_swept_path.py
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: fit curves into Swept Beam nodes

Chains morph wire edges, fits points greedily into straight pieces and plan
arcs with a linear rise, applies a slope along the plan length, and parks
the grip helpers the same way the GDL does.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `create_swept_beam` core

**Files:**
- Create: `src/archicad_mcp/core/swept_beam.py`
- Test: `tests/test_swept_beam_tool.py`

**Interfaces:**
- Consumes: everything in `swept_path` (Task 8); `ArchicadConnection.tapir(command, params) -> dict`; Tapir `GetDetailsOfElements`, `GetAvailableLibraryParts`, `GetAttributesByType`, `CreateObjects`, `SetGDLParametersOfElements`.
- Produces: `swept_beam.create_swept_beam(conn, source_guid=None, points=None, start_height=None, slope_percent=None, profile=None, offset_u=0.0, offset_w=0.0, flip=False, path_tolerance=0.002, update_guid=None, dry_run=True) -> dict` (returns `{"error": ...}` for input and path problems); `swept_beam.LIBRARY_PART = "Swept Beam"`.

- [ ] **Step 1: Write the failing tests** (`tests/test_swept_beam_tool.py`)

```python
"""create_swept_beam over a faked Archicad."""

import json
import math
from pathlib import Path

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import swept_beam as sb
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent / "fixtures" / "swept_beam" / "morph_line_mcp_test.json"
LIB = {"libraryParts": [{"guid": "g", "index": 7, "documentName": "Swept Beam",
                         "fileName": "Swept Beam.gsm", "typeId": "Object"}]}


def _details(entry):
    return lambda params: {"detailsOfElements": [entry]}


def _conn(tapir):
    # conn.tapir() first asks the official API whether Tapir is installed
    official = {"API.IsAddOnCommandAvailable": {"available": True}}
    return ArchicadConnection(19724, core=FakeCore(official=official, tapir=tapir))


def _morph_entry():
    raw = json.loads(FIXTURE.read_text())
    return {"type": "Morph", "floorIndex": 0, "details": {
        "origin": raw["origin"], "xAxis": {"x": 1, "y": 0, "z": 0},
        "yAxis": {"x": 0, "y": 1, "z": 0}, "zAxis": {"x": 0, "y": 0, "z": 1},
        "body": {"vertices": raw["vertices"], "polygons": [], "wireEdges": raw["wireEdges"]}}}


def _params(result):
    return {p["name"]: p["value"] for p in result["gdl_parameters"]}


def test_morph_dry_run_reports_fit_and_parameters():
    conn = _conn({"GetDetailsOfElements": _details(_morph_entry()),
                  "GetAvailableLibraryParts": LIB})
    result = sb.create_swept_beam(conn, source_guid="m-1")
    assert result["dry_run"] is True and result["warnings"] == []
    assert result["max_deviation_mm"] <= 2.0
    params = _params(result)
    assert params["nodeX"][0] == 0.0 and params["nodeY"][0] == 0.0 and params["nodeZ"][0] == 0.0
    assert len(params["nodeX"]) == result["nodes"] == len(params["segArc"]) == len(params["insX"])
    assert params["profileMode"] == "Rectangle"
    # chain_wire_edges starts at the smaller free end, vertex 11, the top of the loop
    assert result["origin"]["z"] == pytest.approx(2.68387534)


def test_polyline_arc_becomes_degrees_with_same_sign():
    entry = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 2, "y": 2}, {"x": 5, "y": 2}],
        "arcs": [{"begIndex": 0, "endIndex": 1, "arcAngle": math.pi / 2}],
        "zCoordinate": 3.0}}
    conn = _conn({"GetDetailsOfElements": _details(entry), "GetAvailableLibraryParts": LIB})
    result = sb.create_swept_beam(conn, source_guid="p-1", start_height=0.5, slope_percent=10)
    params = _params(result)
    assert params["segArc"] == pytest.approx([90.0, 0.0, 0.0])
    assert result["origin"]["z"] == pytest.approx(3.5)
    assert params["nodeZ"] == pytest.approx([0.0, 0.1 * math.pi, 0.1 * (math.pi + 3)])


def test_points_keep_heights_and_warn_about_slope():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 1}, {"x": 4, "y": 0, "z": 2}]
    result = sb.create_swept_beam(conn, points=pts, slope_percent=5)
    assert _params(result)["nodeZ"] == [0.0, 1.0]
    assert _params(result)["A"] == pytest.approx(4.0)
    assert any("flat sources only" in w for w in result["warnings"])


def test_needs_exactly_one_source():
    conn = _conn({})
    assert "exactly one" in sb.create_swept_beam(conn)["error"]
    assert "exactly one" in sb.create_swept_beam(conn, source_guid="a", points=[])["error"]


def test_other_element_types_are_refused():
    entry = {"type": "Spline", "floorIndex": 0, "details": {}}
    conn = _conn({"GetDetailsOfElements": _details(entry)})
    assert "Spline" in sb.create_swept_beam(conn, source_guid="s-1")["error"]


def test_missing_library_part_warns_and_refuses():
    tapir = {"GetAvailableLibraryParts": {"libraryParts": []}}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    dry = sb.create_swept_beam(conn, points=pts)
    assert any("not loaded" in w for w in dry["warnings"])
    committed = sb.create_swept_beam(conn, points=pts, dry_run=False)
    assert "not loaded" in committed["error"]
    assert all(cmd != "CreateObjects" for cmd, _ in conn._core.calls)


def test_commit_places_and_writes_parameters():
    tapir = {"GetAvailableLibraryParts": LIB,
             "CreateObjects": {"elements": [{"elementId": {"guid": "new-1"}}]},
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 10, "y": 5, "z": 2}, {"x": 13, "y": 5, "z": 2.3}]
    result = sb.create_swept_beam(conn, points=pts, dry_run=False)
    assert result["element"] == "new-1" and result["created"] is True
    calls = dict(conn._core.calls)
    assert calls["CreateObjects"]["objectsData"][0]["coordinates"] == {"x": 10, "y": 5, "z": 2}
    written = calls["SetGDLParametersOfElements"]["elementsWithGDLParameters"][0]
    assert written["elementId"] == {"guid": "new-1"}
    names = [p["name"] for p in written["gdlParameters"]]
    assert names[:8] == ["nodeX", "nodeY", "nodeZ", "nodeRoll", "segArc", "segSag", "insX", "insY"]


def test_update_refuses_other_elements():
    other = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Chair"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(other), "GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="chair-1", dry_run=False)
    assert "not a Swept Beam" in result["error"]
    assert all(cmd != "SetGDLParametersOfElements" for cmd, _ in conn._core.calls)


def test_update_uses_the_existing_frame():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 10, "y": 0, "z": 1},
        "angle": math.pi / 2}}
    tapir = {"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 10, "y": 0, "z": 1}, {"x": 10, "y": 3, "z": 1}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert result["updated"] is True and result["element"] == "b-1"
    written = dict(conn._core.calls)["SetGDLParametersOfElements"]
    values = {p["name"]: p["value"] for p in written["elementsWithGDLParameters"][0]["gdlParameters"]}
    assert values["nodeX"] == pytest.approx([0.0, 3.0]) and values["nodeY"] == pytest.approx([0.0, 0.0])
    assert all(cmd != "CreateObjects" for cmd, _ in conn._core.calls)


def test_profile_attribute_by_name():
    attrs = {"attributes": [{"index": 12, "name": "SB Hollow"}, {"index": 3, "name": "Other"}]}
    tapir = {"GetAvailableLibraryParts": LIB, "GetAttributesByType": attrs}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts, profile={"attribute": "sb hollow"}))
    assert params["profileMode"] == "Profile attribute" and params["beamProfile"] == 12
    missing = sb.create_swept_beam(conn, points=pts, profile={"attribute": "Hollow"})
    assert "No Profile attribute named 'Hollow'" in missing["error"] and "SB Hollow" in missing["error"]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_swept_beam_tool.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement** (`src/archicad_mcp/core/swept_beam.py`)

```python
"""create_swept_beam: place a Swept Beam along a curve (spec Part 3).

The Swept Beam library part (gdl-src/swept-beam) stores its path as node
arrays in its own coordinates. This module reads a source curve from the
model or takes 3D points, fits it into nodes with swept_path, and writes the
nodes into a new or an existing Swept Beam.

Tapir reports z as an absolute elevation (story elevation plus offset) for
morphs, polylines, lines, arcs and placed objects, and CreateObjects derives
the story from an absolute z, so this module works in absolute z throughout.
"""

from __future__ import annotations

import math

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import swept_path as sp

LIBRARY_PART = "Swept Beam"
NEAR_VERTICAL_DEG = 85.0
NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Build it with "
              "'archicad-gdl build-source gdl-src/swept-beam --out <GDL workspace>' "
              "into the linked GDL workspace folder, then reload libraries.")


class SweptBeamError(ValueError):
    """The request cannot become a Swept Beam."""


def create_swept_beam(conn: ArchicadConnection, source_guid: str | None = None,
                      points: list[dict] | None = None, start_height: float | None = None,
                      slope_percent: float | None = None, profile: dict | None = None,
                      offset_u: float = 0.0, offset_w: float = 0.0, flip: bool = False,
                      path_tolerance: float = 0.002, update_guid: str | None = None,
                      dry_run: bool = True) -> dict:
    try:
        return _create(conn, source_guid, points, start_height, slope_percent, profile,
                       offset_u, offset_w, flip, path_tolerance, update_guid, dry_run)
    except (sp.PathError, SweptBeamError) as exc:
        return {"error": str(exc)}


def _create(conn, source_guid, points, start_height, slope_percent, profile,
            offset_u, offset_w, flip, path_tolerance, update_guid, dry_run) -> dict:
    if (source_guid is None) == (points is None):
        raise SweptBeamError("Give exactly one of source_guid or points.")
    if path_tolerance <= 0:
        raise SweptBeamError("path_tolerance must be a positive length in metres.")
    warnings: list[str] = []
    if source_guid is not None:
        path, floor_index, flat = _path_from_element(conn, source_guid, path_tolerance)
    else:
        path, floor_index, flat = _path_from_points(points, path_tolerance), None, False
    if flat:
        base = path.nodes[0][2] + (start_height or 0.0)
        path.nodes = sp.apply_slope(path.nodes, path.arcs, base, slope_percent or 0.0)
    elif start_height is not None or slope_percent is not None:
        warnings.append("start_height and slope_percent apply to flat sources only; "
                        "this source keeps its own heights.")
    steepest = path.steepest_slope_deg()
    if steepest > NEAR_VERTICAL_DEG:
        warnings.append(f"A piece rises at {steepest:.1f} degrees. Near vertical, the "
                        "plumb section orientation is undefined.")
    profile_params = _profile_params(conn, profile, offset_u, offset_w, flip)
    loaded = _library_part_loaded(conn)
    if not loaded:
        warnings.append(NOT_LOADED)
    if update_guid is not None:
        origin, angle = _existing_frame(conn, update_guid)
    else:
        origin, angle = path.nodes[0], 0.0
    local = _to_local(path.nodes, origin, angle)
    params = _node_params(local, path.arcs, path_tolerance) + profile_params
    report = {
        "dry_run": dry_run,
        "nodes": len(path.nodes),
        "arcs": path.arc_count,
        "straight": path.straight_count,
        "max_deviation_mm": round(path.max_deviation * 1000, 2),
        "steepest_slope_deg": round(steepest, 1),
        "origin": {"x": origin[0], "y": origin[1], "z": origin[2]},
        "story": floor_index,
        "warnings": warnings,
    }
    if dry_run:
        report["gdl_parameters"] = params
        return report
    if not loaded:
        raise SweptBeamError(NOT_LOADED)
    guid = update_guid or _place(conn, origin, floor_index)
    _write_parameters(conn, guid, params)
    report["element"] = guid
    report["updated" if update_guid else "created"] = True
    return report


def _details(conn, guid: str) -> dict:
    result = conn.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (result.get("detailsOfElements") or [{}])[0]
    if "error" in entry or "type" not in entry:
        raise SweptBeamError(f"Could not read element {guid}: {entry.get('error', entry)}")
    return entry


def _path_from_element(conn, guid: str, tol: float):
    entry = _details(conn, guid)
    kind, d, floor = entry["type"], entry.get("details", {}), entry.get("floorIndex")
    if kind == "Morph":
        return _morph_path(d, tol), floor, False
    if kind == "PolyLine":
        return _polyline_path(d), floor, True
    if kind == "Line":
        z = float(d.get("zCoordinate", 0.0))
        b, e = d["begCoordinate"], d["endCoordinate"]
        return sp.SweptPath([(b["x"], b["y"], z), (e["x"], e["y"], z)], [0.0, 0.0]), floor, True
    if kind == "Arc":
        return _arc_path(d), floor, True
    raise SweptBeamError(
        f"Element {guid} is a {kind}. The source must be a Morph line, Polyline, Line "
        "or Arc (Splines are not supported: their curve shape is not readable).")


def _morph_path(d: dict, tol: float) -> sp.SweptPath:
    body = d.get("body") or {}
    if body.get("polygons"):
        raise SweptBeamError("This morph has faces. Draw the guide as a morph line (edges only).")
    order = sp.chain_wire_edges([tuple(e["vertexIds"]) for e in body.get("wireEdges", [])])
    o = d["origin"]
    ax = d.get("xAxis") or {"x": 1, "y": 0, "z": 0}
    ay = d.get("yAxis") or {"x": 0, "y": 1, "z": 0}
    az = d.get("zAxis") or {"x": 0, "y": 0, "z": 1}
    verts = body["vertices"]
    pts = []
    for i in order:
        v = verts[i]
        pts.append(tuple(o[k] + v["x"] * ax[k] + v["y"] * ay[k] + v["z"] * az[k]
                         for k in ("x", "y", "z")))
    return sp.fit_points(pts, tol)


def _polyline_path(d: dict) -> sp.SweptPath:
    coords = d.get("coordinates") or []
    if len(coords) < 2:
        raise SweptBeamError("The polyline has fewer than two points.")
    z = float(d.get("zCoordinate", 0.0))
    pts = [(float(c["x"]), float(c["y"]), z) for c in coords]
    if len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < sp.DUPLICATE_EPS:
        raise SweptBeamError("The polyline is closed. A Swept Beam needs a start and an end.")
    arcs = [0.0] * len(pts)
    for a in d.get("arcs") or []:
        if a["endIndex"] == a["begIndex"] + 1:
            arcs[a["begIndex"]] = math.degrees(a["arcAngle"])
    return sp.SweptPath(pts, arcs)


def _arc_path(d: dict) -> sp.SweptPath:
    if abs(float(d.get("ratio", 1.0)) - 1.0) > 1e-6:
        raise SweptBeamError("Elliptical arcs are not supported.")
    z = float(d.get("zCoordinate", 0.0))
    ox, oy, r = d["origin"]["x"], d["origin"]["y"], float(d["radius"])
    beg, end = float(d["begAngle"]), float(d["endAngle"])
    sweep = (end - beg) % (2 * math.pi)
    if sweep < 1e-9:
        raise SweptBeamError("The arc has no length.")
    p1 = (ox + r * math.cos(beg), oy + r * math.sin(beg), z)
    p2 = (ox + r * math.cos(end), oy + r * math.sin(end), z)
    return sp.SweptPath([p1, p2], [math.degrees(sweep), 0.0])


def _path_from_points(points, tol: float) -> sp.SweptPath:
    try:
        pts = [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise SweptBeamError("points must be a list of {x, y, z} numbers in metres.") from exc
    return sp.fit_points(pts, tol)


def _attribute_index(conn, kind: str, name: str) -> int:
    attrs = conn.tapir("GetAttributesByType", {"attributeType": kind}).get("attributes", [])
    for a in attrs:
        if a.get("name") == name:
            return int(a["index"])
    for a in attrs:
        if str(a.get("name", "")).casefold() == name.casefold():
            return int(a["index"])
    close = sorted(str(a.get("name")) for a in attrs
                   if name.casefold() in str(a.get("name", "")).casefold()
                   or str(a.get("name", "")).casefold() in name.casefold())[:10]
    hint = f" Similar names: {', '.join(close)}." if close else ""
    raise SweptBeamError(f"No {kind} attribute named '{name}'.{hint}")


def _profile_params(conn, profile, offset_u, offset_w, flip) -> list[dict]:
    common = [{"name": "profileOffsetU", "value": float(offset_u)},
              {"name": "profileOffsetW", "value": float(offset_w)},
              {"name": "flipProfile", "value": bool(flip)}]
    profile = profile or {"rectangle": {"width": 0.2, "height": 0.2}}
    if "attribute" in profile:
        index = _attribute_index(conn, "Profile", str(profile["attribute"]))
        return [{"name": "profileMode", "value": "Profile attribute"},
                {"name": "beamProfile", "value": index}] + common
    if "rectangle" in profile:
        rect = profile["rectangle"] or {}
        w, h = float(rect.get("width", 0.2)), float(rect.get("height", 0.2))
        if w <= 0 or h <= 0:
            raise SweptBeamError("Rectangle width and height must be positive.")
        out = [{"name": "profileMode", "value": "Rectangle"},
               {"name": "rectW", "value": w}, {"name": "rectH", "value": h}]
        if rect.get("building_material"):
            out.append({"name": "rectBMat", "value": _attribute_index(
                conn, "BuildingMaterial", str(rect["building_material"]))})
        return out + common
    raise SweptBeamError('profile must be {"attribute": "<name>"} or '
                         '{"rectangle": {"width": w, "height": h, "building_material": "<name>"}}.')


def _library_part_loaded(conn) -> bool:
    parts = conn.tapir("GetAvailableLibraryParts",
                       {"filterByTypeId": "Object"}).get("libraryParts", [])
    want = LIBRARY_PART.casefold()
    return any(str(p.get("documentName", "")).casefold() == want
               or str(p.get("fileName", "")).casefold() == want + ".gsm" for p in parts)


def _existing_frame(conn, guid: str):
    entry = _details(conn, guid)
    d = entry.get("details", {})
    name = str((d.get("libPart") or {}).get("name", ""))
    if entry["type"] != "Object" or name.casefold() != LIBRARY_PART.casefold():
        raise SweptBeamError(f"Element {guid} is not a Swept Beam. update_guid must "
                             "point at a placed Swept Beam.")
    o = d["origin"]
    return (float(o["x"]), float(o["y"]), float(o["z"])), float(d.get("angle", 0.0))


def _to_local(nodes, origin, angle: float) -> list[sp.Point]:
    c, s = math.cos(-angle), math.sin(-angle)
    out = []
    for x, y, z in nodes:
        dx, dy = x - origin[0], y - origin[1]
        out.append((dx * c - dy * s, dx * s + dy * c, z - origin[2]))
    return out


def _node_params(local, arcs, tol: float) -> list[dict]:
    sag, ins_x, ins_y = sp.grip_helpers(local, arcs)

    def r6(values):
        return [round(v, 6) + 0.0 for v in values]

    params = [
        {"name": "nodeX", "value": r6(p[0] for p in local)},
        {"name": "nodeY", "value": r6(p[1] for p in local)},
        {"name": "nodeZ", "value": r6(p[2] for p in local)},
        {"name": "nodeRoll", "value": [0.0] * len(local)},
        {"name": "segArc", "value": r6(arcs)},
        {"name": "segSag", "value": r6(sag)},
        {"name": "insX", "value": r6(ins_x)},
        {"name": "insY", "value": r6(ins_y)},
        {"name": "pathTolerance", "value": tol},
    ]
    if len(local) == 2 and abs(arcs[0]) <= sp.ARC_EPS_DEG:
        params.append({"name": "A", "value": round(math.hypot(local[1][0] - local[0][0],
                                                              local[1][1] - local[0][1]), 6)})
    return params


def _place(conn, origin, floor_index) -> str:
    item = {"libraryPartName": LIBRARY_PART,
            "coordinates": {"x": origin[0], "y": origin[1], "z": origin[2]}}
    if floor_index is not None:
        item["floorIndex"] = floor_index
    result = conn.tapir("CreateObjects", {"objectsData": [item]})
    elements = result.get("elements") or []
    if not elements or "elementId" not in elements[0]:
        raise SweptBeamError(f"CreateObjects did not place '{LIBRARY_PART}': {result}")
    return elements[0]["elementId"]["guid"]


def _write_parameters(conn, guid: str, params: list[dict]) -> None:
    result = conn.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
        {"elementId": {"guid": guid}, "gdlParameters": params}]})
    first = (result.get("executionResults") or [{}])[0]
    if not first.get("success", False):
        raise SweptBeamError(f"Writing the Swept Beam parameters of element {guid} failed "
                             f"(the element exists): {first.get('error', first)}")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_swept_beam_tool.py tests/test_swept_path.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/swept_beam.py tests/test_swept_beam_tool.py
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: create_swept_beam core

Reads a morph line, polyline, line or arc (or takes 3D points), fits the
nodes, and places a Swept Beam or rewrites one in place. Dry run reports the
fit; commit refuses when the library part is not loaded.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Register the tool, document it, Gate D acceptance

**Files:**
- Modify: `src/archicad_mcp/server.py:385-397` (next to `create_elements`)
- Modify: `manifest.json` (tools list, after `create_elements`), `README.md:413-418`, `tests/test_tool_annotations.py:32-40`
- Create: `docs/swept-beam.md`
- Test: `tests/test_manifest.py`, `tests/test_tool_annotations.py` (existing tests enforce registration, manifest and hints)

**Interfaces:**
- Consumes: `swept_beam.create_swept_beam` (Task 9).
- Produces: MCP tool `create_swept_beam` in full mode.

- [ ] **Step 1: Make the existing guard tests fail first**

Add `"create_swept_beam"` to the `WRITERS` set in `tests/test_tool_annotations.py` and add to `manifest.json` after the `create_elements` entry:

```json
    {
      "name": "create_swept_beam",
      "description": "Place a Swept Beam (sloped, curved, custom profile) along a morph line, polyline, line, arc or 3D points. Dry-run by default."
    },
```

Run: `uv run pytest tests/test_manifest.py tests/test_tool_annotations.py -v`
Expected: FAIL (the manifest lists a tool the server does not register).

- [ ] **Step 2: Register the tool** in `src/archicad_mcp/server.py`, directly after the `create_elements` tool, and add `from archicad_mcp.core import swept_beam as _swept` beside the other core imports there:

```python
    @mcp.tool(description=(
        "Place a Swept Beam: a sloped, curved beam with a Profile Manager profile "
        "or a rectangle, as the 'Swept Beam' library part. The path comes from "
        "source_guid (a Morph line, Polyline, Line or Arc) or from points "
        "[{x, y, z}] in metres. Flat sources take start_height (metres above the "
        "source) and slope_percent; morph lines and points keep their own heights. "
        "profile is {\"attribute\": \"<Profile name>\"} or {\"rectangle\": {\"width\", "
        "\"height\", \"building_material\"}} (default 0.2 x 0.2 rectangle). "
        "update_guid rewrites the path of an existing Swept Beam, keeping its GUID. "
        "DRY-RUN BY DEFAULT: reports nodes, arcs, the maximum deviation from the "
        "source and warnings. Pass dry_run=false to place or update."),
        **_tool_meta("Create Swept Beam", read_only=False, destructive=True))
    @_guarded
    def create_swept_beam(source_guid: str | None = None, points: list[dict] | None = None,
                          start_height: float | None = None,
                          slope_percent: float | None = None, profile: dict | None = None,
                          offset_u: float = 0.0, offset_w: float = 0.0, flip: bool = False,
                          path_tolerance: float = 0.002, update_guid: str | None = None,
                          dry_run: bool = True, port: int | None = None) -> dict:
        return _swept.create_swept_beam(_conn(port), source_guid, points, start_height,
                                        slope_percent, profile, offset_u, offset_w, flip,
                                        path_tolerance, update_guid, dry_run)
```

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS (live tests deselected).

- [ ] **Step 4: README and team doc**

In `README.md`, in the "Core (full mode)" list, change `` `set_element_data`, `create_elements`, `` to `` `set_element_data`, `create_elements`, `create_swept_beam`, ``.

Create `docs/swept-beam.md`:

```markdown
# Swept Beam

A library part for sloped, curved beams with any Profile Manager profile. It
edits like a native element: move nodes, curve edges, insert and merge nodes,
lift nodes, all with grips. Built as an Archicad 27 part, so the same file
works in Archicad 27, 28, 29 and 30.

## Install

1. Build it: `uv run archicad-gdl build-source gdl-src/swept-beam --out <folder>`.
2. Put `Swept Beam.gsm` in the office library (and the BIMcloud library for
   Teamwork projects), or add the output folder in Library Manager.
3. Never open and save it in the Library Part Editor: saving in a newer
   Archicad makes it unreadable for older ones. Change the source in
   `gdl-src/swept-beam` and build again.

## Draw

- Place it with the Object tool using the <placement method from gate C>
  method: first click is the start, second the end.
- Move a node: drag its grip in plan, or in 3D to lift it.
- Curve an edge: drag the grip at the middle of a segment sideways.
- Insert a node: drag the grip at one third of a segment to where the new
  node goes.
- Delete a node: drag it onto its neighbour.
- Heights: set Slope (%) on the Path page and press Apply slope; then drag
  single nodes in 3D.
- Exact values and roll: the node arrays in the parameter list.

## Profile

On the Profile page choose "Profile attribute" and pick any profile. Its
components keep their building materials in sections. If the profile is
missing in a project, the beam falls back to the rectangle.

## From a curve (MCP)

`create_swept_beam` turns a Morph line, Polyline, Line or Arc, or 3D points
from a DWG or Rhino, into a Swept Beam. Always run it as a dry run first: it
reports how many nodes it made and how far they are from the source.
```

Replace `<placement method from gate C>` with the method recorded in gate C step 7 before committing.

- [ ] **Step 5: Gate D acceptance (live).** Restart the MCP server so it registers the new tool (ask the user to restart the Claude extension), then on the MCP-Test port (confirm `project_name`):
  1. `create_swept_beam(source_guid="0EF502A3-378D-964C-8FBA-44EB4A052F22")` (the test morph line): dry run shows `max_deviation_mm <= 2`, no warnings. Then `dry_run=false`: render the new element with `GetElementPreviewImage`; ask the user to confirm it follows the morph line in 3D, including the -55 degree loop and the three kinks. If the beam is offset vertically from the line, morph vertex z is not relative to the origin as assumed: record it and fix `_morph_path`.
  2. Same element, `create_swept_beam(source_guid=<morph>, update_guid=<new beam>, profile={"attribute": "<hollow profile name>"}, dry_run=false)`: GUID unchanged, section shows the hole.
  3. Ask the user to draw a Polyline with one counter-clockwise arc and one clockwise arc and an Arc element; convert each: the beams follow the drawn curves exactly (arc sign check, Review Focus 1).
  4. Archicad 27 port (confirm `project_name` is `MCP-Test_27`): repeat step 1 with points read from the same fixture (`points` argument), since that file has no morph line.
  Record all results in `## Gate D` of the findings doc.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/server.py manifest.json README.md tests/test_tool_annotations.py docs/swept-beam.md docs/superpowers/specs/2026-09-29-swept-beam-findings.md
git -c user.name="Aleš Dolenec" -c user.email="285164556+alesdev88@users.noreply.github.com" commit -m "feat: create_swept_beam MCP tool, Swept Beam team guide

Registered in full mode, dry run by default, annotated destructive. Gate D
acceptance recorded: the MCP-Test morph line, an in-place profile swap,
polyline and arc signs, and Archicad 27.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the plan

Releasing a new MCP version (version bump, bundle, tag, PyPI) is a separate step the user starts; it is not part of this plan.
