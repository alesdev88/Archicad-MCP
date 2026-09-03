# GDL MCP Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the GDL library-part pipeline as four MCP tools so a client whose agent has no shell on the Archicad machine can build and deploy a `.gsm`.

**Architecture:** The `gdl` package stays a pure library. A new `gdl/workspace.py` contains every path inside one configured root, and a new `gdl/tools.py` registers the tools and adapts library exceptions to the server's `{"error": ...}` envelope. `build_server` calls `_register_gdl_tools` the way it already calls `_register_full_mode_tools`. Delivery is a linked library folder: the workspace root doubles as an Archicad library, so a rebuild overwrites the `.gsm` under a stable GUID and `ReloadLibraries` updates placed instances.

**Tech Stack:** Python 3.12, fastmcp 3.4.4, pytest 8 (asyncio_mode=auto), Pillow (added by this plan), LP_XMLConverter (ships inside Archicad), Blender (optional, decimation only).

**Spec:** `docs/superpowers/specs/2026-09-02-gdl-mcp-tools-design.md`

## Global Constraints

- Python `>=3.12`. Every new module starts with `from __future__ import annotations`.
- **Never write to stdout from server code.** Under stdio transport stdout is the JSON-RPC channel and one stray byte drops the server. Diagnostics go to stderr.
- **No em dashes or en dashes** anywhere: code, comments, docstrings, docs, commit messages. Rewrite the sentence rather than swapping the character.
- Every MCP tool is registered with `**_tool_meta(title, read_only=..., destructive=...)` and wrapped in `@_guarded`. This codebase reads "destructive" as "changes the project or writes a file", which is wider than the MCP spec.
- GDL tools are **full mode only**, and only when a workspace folder is configured.
- Tests run with `uv run pytest` (the `addopts = "-m 'not live'"` in pyproject excludes live tests). Live tests run with `ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v`.
- Never run a live test against a model the user cares about without asking first.
- Commit after every task.

---

### Task 1: Live probe of the linked-library assumption

**This task gates every other task. Do not start Task 2 until it passes or the fallback is recorded.**

The whole delivery mechanism rests on two things that are documented in `docs/gdl-pipeline.md` but never demonstrated: that `ReloadLibraries` alone picks up a `.gsm` newly written into a linked library folder, and that `CreateObjects` then resolves it by name. The demonstrated path was `AddFilesToEmbeddedLibrary`.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-02-gdl-mcp-tools-design.md` (record the result)

**Interfaces:**
- Consumes: nothing
- Produces: a recorded yes/no that Tasks 6 and 9 depend on

- [ ] **Step 1: Ask the user for a scratch project and a port**

Ask for a small, non-sensitive model and the port it answers on. Do not proceed without an explicit go-ahead: this task places and deletes a real element.

- [ ] **Step 2: Build a throwaway .gsm with the existing CLI**

On a machine with a local shell:

```bash
mkdir -p /tmp/gdl-probe
archicad-gdl build "Test chair/<some source>.3ds" --name ProbeChair --out /tmp/gdl-probe
```

Expected: `/tmp/gdl-probe/ProbeChair.gsm` exists.

- [ ] **Step 3: Add the folder as a linked library, by hand**

In Archicad: File > Libraries and Objects > Library Manager, add `/tmp/gdl-probe`, then OK. This is the one-time step no API can perform, and the spec keeps it as a documented prerequisite.

- [ ] **Step 4: Reload and place through the existing MCP tools**

Use `execute_write_api_command` with the Tapir command `ReloadLibraries` and no parameters, then `create_elements` with `element_type="object"`, `items=[{"libraryPartName": "ProbeChair", "coordinates": {"x": 0, "y": 0, "z": 0}}]`, `dry_run=false`.

Expected: an element GUID comes back and the object is visible in the 3D window.

- [ ] **Step 5: Probe the overwrite half of the assumption**

Rebuild with a visible change (edit one group's `rgb` in a config, or rebuild with `--name ProbeChair` from a different source), overwriting `/tmp/gdl-probe/ProbeChair.gsm` on disk. Run `ReloadLibraries` again.

Expected: the already-placed instance updates in place, without being deleted and re-placed. This is the property that makes iteration bearable.

- [ ] **Step 6: Clean up**

Delete the placed element with `delete_elements(guids=[...], confirm=true)`. Confirm the project element count matches what it was before Step 4.

- [ ] **Step 7: Record the outcome in the spec and commit**

If both halves pass, add a line under "The assumption this design rests on" stating it is now demonstrated, with the date.

If either fails, record what actually happened and switch the plan's default: `deploy_gdl_object` gains `embed=True` as its default, Task 9 calls `deploy_mod.embed_gsm` and `deploy_mod.embed_textures` before reloading, and the tool's description must warn that iterating inside one project needs fresh names, because Tapir cannot overwrite an existing embedded file and fails with a misleading "outputPath is not a valid relative path".

```bash
git add docs/superpowers/specs/2026-09-02-gdl-mcp-tools-design.md
git commit -m "docs: record the linked-library probe result"
```

---

### Task 2: Windows discovery for LP_XMLConverter and Blender

`find_lp_xmlconverter` currently globs `/Applications/Graphisoft` and nothing else, so it raises `ToolchainError` immediately on Windows. Same for `find_blender`. The target operator is on Windows, so this is required, not a nicety.

**Files:**
- Modify: `src/archicad_mcp/gdl/toolchain.py:24-41` (`find_lp_xmlconverter`), `:118-123` (`find_blender`)
- Test: `tests/test_gdl_toolchain.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `find_lp_xmlconverter() -> Path`, `find_blender() -> Path | None` (signatures unchanged), plus `_archicad_roots() -> list[Path]` and `_blender_roots() -> list[Path]` as monkeypatch seams for tests

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdl_toolchain.py`:

```python
"""Toolchain discovery across platforms: env override first, then install roots."""

import pytest

from archicad_mcp.gdl import toolchain


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_env_override_wins(tmp_path, monkeypatch):
    exe = _touch(tmp_path / "LP_XMLConverter")
    monkeypatch.setenv("LP_XMLCONVERTER", str(exe))
    assert toolchain.find_lp_xmlconverter() == exe


def test_env_override_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LP_XMLCONVERTER", str(tmp_path / "nope"))
    with pytest.raises(toolchain.ToolchainError, match="missing file"):
        toolchain.find_lp_xmlconverter()


def test_windows_layout_picks_newest(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    root = tmp_path / "GRAPHISOFT"
    _touch(root / "Archicad 28" / "LP_XMLConverter.exe")
    newest = _touch(root / "Archicad 29" / "LP_XMLConverter.exe")
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [root])
    assert toolchain.find_lp_xmlconverter() == newest


def test_macos_layout_picks_newest(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    root = tmp_path / "Graphisoft"
    rel = "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"
    _touch(root / "Archicad 28" / "Archicad 28.app" / rel)
    newest = _touch(root / "Archicad 29" / "Archicad 29.app" / rel)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [root])
    assert toolchain.find_lp_xmlconverter() == newest


def test_not_found_names_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("LP_XMLCONVERTER", raising=False)
    monkeypatch.setattr(toolchain, "_archicad_roots", lambda: [tmp_path])
    with pytest.raises(toolchain.ToolchainError, match="LP_XMLCONVERTER"):
        toolchain.find_lp_xmlconverter()


def test_blender_windows_layout(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    exe = _touch(tmp_path / "Blender Foundation" / "Blender 4.2" / "blender.exe")
    monkeypatch.setattr(toolchain, "_blender_roots", lambda: [tmp_path])
    assert toolchain.find_blender() == exe


def test_blender_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(toolchain, "_blender_roots", lambda: [tmp_path])
    assert toolchain.find_blender() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_toolchain.py -v`
Expected: FAIL with `AttributeError: module 'archicad_mcp.gdl.toolchain' has no attribute '_archicad_roots'` (and `toolchain.sys` missing until `sys` is imported).

- [ ] **Step 3: Implement the platform branches**

In `src/archicad_mcp/gdl/toolchain.py`, add `import sys` to the imports, then replace `find_lp_xmlconverter` and `find_blender`:

```python
# (glob pattern under an install root, path from the match to the binary)
_LP_LAYOUT = {
    "win32": ("Archicad *", "LP_XMLConverter.exe"),
    "darwin": ("Archicad */Archicad *.app",
               "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"),
}


def _archicad_roots() -> list[Path]:
    """Where Archicad installs live. A seam, so tests can point elsewhere."""
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [Path(program_files) / "GRAPHISOFT"]
    return [Path("/Applications/Graphisoft")]


def _blender_roots() -> list[Path]:
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [Path(program_files)]
    return [Path("/Applications")]


def find_lp_xmlconverter() -> Path:
    """Locate LP_XMLConverter: env override first, then the newest Archicad."""
    env = os.environ.get("LP_XMLCONVERTER")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise ToolchainError(f"LP_XMLCONVERTER points to a missing file: {env}")
    pattern, tail = _LP_LAYOUT.get(sys.platform, _LP_LAYOUT["darwin"])
    candidates = []
    for root in _archicad_roots():
        for entry in root.glob(pattern):
            exe = entry / tail
            if exe.is_file():
                m = re.search(r"Archicad (\d+)", entry.name)
                candidates.append((int(m.group(1)) if m else 0, exe))
    if not candidates:
        looked = ", ".join(str(r) for r in _archicad_roots())
        raise ToolchainError(
            f"LP_XMLConverter not found under {looked}. Install Archicad or set "
            "the LP_XMLCONVERTER environment variable to the binary inside the "
            "Archicad installation.")
    return max(candidates)[1]


def find_blender() -> Path | None:
    env = os.environ.get("BLENDER")
    if env and Path(env).is_file():
        return Path(env)
    if sys.platform == "win32":
        pattern, tail = "Blender Foundation/Blender *", "blender.exe"
    else:
        pattern, tail = "Blender.app", "Contents/MacOS/Blender"
    for root in _blender_roots():
        for entry in sorted(root.glob(pattern), reverse=True):
            exe = entry / tail
            if exe.is_file():
                return exe
    return None
```

Note `max(candidates)` compares tuples, so a version tie falls through to comparing `Path`, which is fine and deterministic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_toolchain.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the whole suite for regressions**

Run: `uv run pytest`
Expected: PASS. `find_blender`'s macOS path changed shape (it now globs rather than checking one literal path) so anything mocking it must still work.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/gdl/toolchain.py tests/test_gdl_toolchain.py
git commit -m "fix: find LP_XMLConverter and Blender on Windows too

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Cross-platform texture downscaling with Pillow

`_downscale` shells out to `sips`, which exists only on macOS, and swallows the failure. On Windows every texture ships at source resolution. One raw veneer jpg in the test assets is 9.5 MB at 3000px, and the point of shipping textures as real library files is that Enscape and Twinmotion load them.

**Files:**
- Modify: `src/archicad_mcp/gdl/generate.py:200-205` (`_downscale`), imports
- Modify: `pyproject.toml` (add `pillow>=10`)
- Test: `tests/test_gdl_generate.py` (append)

**Interfaces:**
- Consumes: `MAX_TEXTURE_PX` (already defined at `generate.py:41`)
- Produces: `_downscale(dst: Path) -> None`, unchanged signature, now platform-independent

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `dependencies`:

```toml
    "pillow>=10",
```

Run: `uv sync`

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_gdl_generate.py`:

```python
def test_downscale_caps_the_long_edge(tmp_path):
    from PIL import Image as PILImage

    from archicad_mcp.gdl.generate import MAX_TEXTURE_PX, _downscale

    src = tmp_path / "big.jpg"
    PILImage.new("RGB", (3000, 1500), (120, 90, 60)).save(src)
    _downscale(src)
    with PILImage.open(src) as img:
        assert max(img.size) == MAX_TEXTURE_PX
        assert img.size == (MAX_TEXTURE_PX, MAX_TEXTURE_PX // 2)


def test_downscale_leaves_small_images_alone(tmp_path):
    from PIL import Image as PILImage

    from archicad_mcp.gdl.generate import _downscale

    src = tmp_path / "small.png"
    PILImage.new("RGB", (256, 256), (10, 20, 30)).save(src)
    before = src.read_bytes()
    _downscale(src)
    assert src.read_bytes() == before


def test_downscale_ignores_a_file_it_cannot_read(tmp_path):
    from archicad_mcp.gdl.generate import _downscale

    junk = tmp_path / "notreally.jpg"
    junk.write_text("this is not an image")
    _downscale(junk)  # must not raise: a bad texture is not a build failure
    assert junk.read_text() == "this is not an image"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_generate.py -k downscale -v`
Expected: FAIL. The first test fails because `sips` either is absent or leaves the size unchanged in the tmp dir; the third may pass by accident, which is fine.

- [ ] **Step 4: Replace the sips call**

In `src/archicad_mcp/gdl/generate.py`, drop `import subprocess` if nothing else uses it (check first with `grep -n subprocess src/archicad_mcp/gdl/generate.py`), and replace `_downscale`:

```python
def _downscale(dst: Path) -> None:
    """Cap the long edge at MAX_TEXTURE_PX, in place, keeping the format.

    Pillow rather than a platform image tool: the previous `sips` call worked
    only on macOS and its failure was swallowed, so Windows silently shipped
    source-resolution textures. A texture that cannot be read is not a build
    failure, so it ships as-is.
    """
    try:
        from PIL import Image as PILImage

        with PILImage.open(dst) as img:
            if max(img.size) <= MAX_TEXTURE_PX:
                return
            img.thumbnail((MAX_TEXTURE_PX, MAX_TEXTURE_PX))
            extra = {"quality": 90} if dst.suffix.lower() in (".jpg", ".jpeg") else {}
            img.save(dst, **extra)
    except OSError:
        pass  # unreadable or unwritable image: ship the original
```

Also update the comment on line 41 so it no longer says `sips -Z`:

```python
MAX_TEXTURE_PX = 1024   # textures are downscaled to fit this long edge
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_generate.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/archicad_mcp/gdl/generate.py tests/test_gdl_generate.py
git commit -m "fix: downscale textures with Pillow so Windows gets it too

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Share one config parser between file and tool argument

`load_config` reads a file and parses in one pass. The build tool takes a config as an inline argument, so the same parse has to be reachable without a file. Without this split the schema exists twice and drifts.

**Files:**
- Modify: `src/archicad_mcp/gdl/config.py:100-125` (`load_config`)
- Test: `tests/test_gdl_config.py` (create)

**Interfaces:**
- Consumes: `ObjectConfig`, `GroupSpec` (already in `config.py`)
- Produces:
  - `parse_objects(raw: dict, base: Path) -> dict[str, ObjectConfig]`
  - `load_config(path: str | Path) -> dict[str, ObjectConfig]` (unchanged behaviour)
  - `save_object_config(path: Path, name: str, spec: dict) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdl_config.py`:

```python
"""Config parsing shared by the file loader and the inline tool argument."""

import json
from pathlib import Path

from archicad_mcp.gdl.config import (
    load_config,
    parse_objects,
    save_object_config,
)

SPEC = {
    "guid": "4E501AE2-172D-4F03-B248-C9C2DE3E641E",
    "source": "chair.obj",
    "textures": {"logo": "maps/logo.jpg"},
    "variants": [{"label": "Natural oak", "roles": {"face": "maps/oak.jpg"}},
                 {"label": "Black", "roles": {"face": [0.09, 0.09, 0.10]}}],
    "frame_variants": [{"label": "Black steel", "rgb": [0.10, 0.10, 0.11]}],
    "groups": {"m1_1": {"label": "Seat", "texture": "face",
                        "rgb": [0.72, 0.58, 0.40], "uv_rotate": 90}},
    "decimate": {"Powder Co": 6000},
}


def test_file_and_inline_parse_identically(tmp_path):
    path = tmp_path / "assets.json"
    path.write_text(json.dumps({"objects": {"Chair": SPEC}}))
    from_file = load_config(path)
    inline = parse_objects({"objects": {"Chair": SPEC}}, tmp_path)
    assert from_file == inline


def test_parse_resolves_paths_against_the_base(tmp_path):
    objects = parse_objects({"objects": {"Chair": SPEC}}, tmp_path)
    cfg = objects["Chair"]
    assert cfg.source == tmp_path / "chair.obj"
    assert cfg.textures["logo"] == tmp_path / "maps" / "logo.jpg"
    assert cfg.variants[0][1]["face"] == tmp_path / "maps" / "oak.jpg"
    assert cfg.variants[1][1]["face"] == (0.09, 0.09, 0.10)
    assert cfg.groups["m1_1"].uv_rotate == 90


def test_save_merges_without_dropping_other_objects(tmp_path):
    path = tmp_path / "assets.json"
    path.write_text(json.dumps({"objects": {"Stool": SPEC}}))
    save_object_config(path, "Chair", SPEC)
    raw = json.loads(path.read_text())
    assert set(raw["objects"]) == {"Stool", "Chair"}


def test_save_creates_the_file_when_absent(tmp_path):
    path = tmp_path / "assets.json"
    save_object_config(path, "Chair", SPEC)
    assert json.loads(path.read_text())["objects"]["Chair"] == SPEC


def test_save_replaces_an_existing_entry(tmp_path):
    path = tmp_path / "assets.json"
    save_object_config(path, "Chair", SPEC)
    save_object_config(path, "Chair", {**SPEC, "decimate": {}})
    assert json.loads(path.read_text())["objects"]["Chair"]["decimate"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_objects'`.

- [ ] **Step 3: Split the parser and add the writer**

In `src/archicad_mcp/gdl/config.py`, replace `load_config` with:

```python
def parse_objects(raw: dict, base: Path) -> dict[str, ObjectConfig]:
    """Parse a raw config mapping. Relative paths resolve against `base`.

    Shared by the file loader and the MCP build tool's inline argument, so the
    schema is defined once. A second copy would drift.
    """
    objects: dict[str, ObjectConfig] = {}
    for name, spec in raw.get("objects", {}).items():
        objects[name] = ObjectConfig(
            name=name,
            guid=spec.get("guid"),
            source=(base / spec["source"]) if spec.get("source") else None,
            textures={k: base / v for k, v in spec.get("textures", {}).items()},
            variants=[(v["label"],
                       {role: _role_value(val, base)
                        for role, val in v.get("roles", {}).items()})
                      for v in spec.get("variants", [])],
            frame_variants=[(v["label"], _as_rgb(v["rgb"]))
                            for v in spec.get("frame_variants", [])],
            groups={sub: GroupSpec(label=g["label"],
                                   texture=g.get("texture"),
                                   rgb=_as_rgb(g.get("rgb", (0.6, 0.6, 0.6))),
                                   uv_rotate=int(g.get("uv_rotate", 0)))
                    for sub, g in spec.get("groups", {}).items()},
            decimate={k: int(v) for k, v in spec.get("decimate", {}).items()},
        )
    return objects


def load_config(path: str | Path) -> dict[str, ObjectConfig]:
    path = Path(path)
    return parse_objects(json.loads(path.read_text()), path.parent)


def save_object_config(path: str | Path, name: str, spec: dict) -> None:
    """Merge one object's raw spec into an assets.json, keeping the rest.

    Read-modify-write of the whole file. The alternative of appending would
    corrupt the JSON, and the file is small enough that rewriting it is free.
    """
    path = Path(path)
    raw = json.loads(path.read_text()) if path.is_file() else {}
    raw.setdefault("objects", {})[name] = spec
    path.write_text(json.dumps(raw, indent=2) + "\n")
```

`ObjectConfig` and `GroupSpec` are dataclasses, so `==` compares field by field and the identity test in Step 1 works without any extra code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_config.py tests/test_gdl_generate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/config.py tests/test_gdl_config.py
git commit -m "refactor: share one config parser between file and inline argument

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Workspace path containment

Every path argument the tools accept is a name relative to one configured root. Anything resolving outside is refused. The alternative, accepting absolute paths, turns the server into an arbitrary file reader on behalf of a cloud-hosted agent.

**Files:**
- Create: `src/archicad_mcp/gdl/workspace.py`
- Test: `tests/test_gdl_workspace.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class WorkspaceError(ValueError)`
  - `class Workspace` with `root: Path`, `resolve(name: str) -> Path`, `require_root() -> Path`, `assets_path() -> Path`, `textures_dir() -> Path`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdl_workspace.py`:

```python
"""Containment: a tool argument can never name a file outside the workspace."""

import pytest

from archicad_mcp.gdl.workspace import Workspace, WorkspaceError


def test_resolves_a_plain_name(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve("chair.3ds") == (tmp_path / "chair.3ds").resolve()


def test_resolves_a_nested_name(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve("maps/oak.jpg") == (tmp_path / "maps" / "oak.jpg").resolve()


def test_root_itself_is_allowed(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.resolve(".") == tmp_path.resolve()


def test_traversal_is_refused(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve("../secrets.txt")


def test_absolute_path_is_refused(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve(str(tmp_path / "elsewhere.txt"))


def test_symlink_out_is_refused(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    ws = Workspace(root)
    with pytest.raises(WorkspaceError, match="outside"):
        ws.resolve("escape/secrets.txt")


def test_empty_name_is_refused(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError, match="Empty"):
        ws.resolve("   ")


def test_require_root_raises_when_missing(tmp_path):
    ws = Workspace(tmp_path / "nope")
    with pytest.raises(WorkspaceError, match="does not exist"):
        ws.require_root()


def test_assets_and_textures_paths(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.assets_path() == (tmp_path / "assets.json").resolve()
    assert ws.textures_dir() == (tmp_path / "textures").resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archicad_mcp.gdl.workspace'`.

- [ ] **Step 3: Implement the workspace**

Create `src/archicad_mcp/gdl/workspace.py`:

```python
"""Path containment for the MCP-facing GDL tools.

The tools run in the server process, on the machine running Archicad, driven
by an agent that may be sandboxed on another host entirely. So every path a
tool accepts is a name relative to one configured root, and anything that
resolves outside it is refused. Containment lives here rather than in each
tool, because a check repeated per call site is a check that eventually gets
forgotten at one of them.

The root doubles as an Archicad linked library folder: builds write the .gsm
and its textures/ here, and ReloadLibraries picks them up.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """A path argument named something outside the workspace, or the root is gone."""


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def resolve(self, name: str) -> Path:
        """Absolute path for a workspace-relative name, or raise.

        `.resolve()` follows symlinks before the containment check, so a link
        inside the workspace pointing out of it is caught rather than followed.
        An absolute `name` lands outside the root and is refused by the same
        check, because `root / "/etc/passwd"` is `/etc/passwd` in pathlib.
        """
        text = str(name).strip()
        if not text:
            raise WorkspaceError("Empty path. Give a name relative to the workspace.")
        resolved = (self.root / text).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(
                f"{name!r} resolves outside the GDL workspace ({self.root}). "
                "Tool arguments are names relative to that folder.")
        return resolved

    def require_root(self) -> Path:
        if not self.root.is_dir():
            raise WorkspaceError(
                f"GDL workspace folder does not exist: {self.root}. Set the "
                "GDL workspace folder in the extension settings to a folder "
                "that exists, and add it to Archicad as a linked library.")
        return self.root

    def assets_path(self) -> Path:
        return self.root / "assets.json"

    def textures_dir(self) -> Path:
        return self.root / "textures"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_workspace.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/workspace.py tests/test_gdl_workspace.py
git commit -m "feat: workspace path containment for the GDL tools

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Preview bytes without a file

`preview_png` writes the render to disk. The deploy tool returns it inline as MCP image content instead, so the bytes are needed without a path. Keep `preview_png` working, since the CLI uses it.

**Files:**
- Modify: `src/archicad_mcp/gdl/deploy.py:64-81` (`preview_png`)
- Test: `tests/test_gdl_deploy.py` (create)

**Interfaces:**
- Consumes: `ArchicadConnection.tapir(command, params)`
- Produces:
  - `preview_image_bytes(conn, element_guid: str, size: int = 700) -> bytes`
  - `preview_png(conn, element_guid, out_path, size=700) -> Path` (unchanged signature, now a thin wrapper)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdl_deploy.py`:

```python
"""Deploy helpers against a fake connection: no Archicad required."""

import base64

from archicad_mcp.gdl import deploy as deploy_mod

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeConn:
    port = 19723

    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append((command, params))
        if command == "GetElementPreviewImage":
            return {"previewImage": base64.b64encode(PNG).decode()}
        if command == "CreateObjects":
            return {"elements": [{"elementId": {"guid": "ABC-123"}}]}
        return {}


def test_preview_image_bytes_decodes_the_payload():
    conn = FakeConn()
    assert deploy_mod.preview_image_bytes(conn, "ABC-123") == PNG


def test_preview_image_bytes_asks_for_the_3d_view():
    conn = FakeConn()
    deploy_mod.preview_image_bytes(conn, "ABC-123", size=512)
    command, params = conn.calls[0]
    assert command == "GetElementPreviewImage"
    assert params["imageType"] == "3D"
    assert params["format"] == "png"
    assert params["width"] == params["height"] == 512
    assert params["elementId"] == {"guid": "ABC-123"}


def test_preview_png_still_writes_a_file(tmp_path):
    conn = FakeConn()
    out = deploy_mod.preview_png(conn, "ABC-123", tmp_path / "check.png")
    assert out.read_bytes() == PNG
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_deploy.py -v`
Expected: FAIL with `AttributeError: module 'archicad_mcp.gdl.deploy' has no attribute 'preview_image_bytes'`.

- [ ] **Step 3: Extract the bytes helper**

In `src/archicad_mcp/gdl/deploy.py`, replace `preview_png` with:

```python
def preview_image_bytes(conn: ArchicadConnection, element_guid: str,
                        size: int = 700) -> bytes:
    """Render the placed element and return the PNG bytes.

    This is the only automated gate that catches defective 3D bodies:
    LP_XMLConverter's interpreter passes scripts whose geometry Archicad
    silently drops, so something has to actually look at the picture.
    """
    result = conn.tapir("GetElementPreviewImage", {
        "elementId": {"guid": element_guid},
        "imageType": "3D",
        "format": "png",
        "width": size,
        "height": size,
    })
    return base64.b64decode(result["previewImage"])


def preview_png(conn: ArchicadConnection, element_guid: str,
                out_path: str | Path, size: int = 700) -> Path:
    """preview_image_bytes, written to a file. Used by the CLI."""
    out_path = Path(out_path)
    out_path.write_bytes(preview_image_bytes(conn, element_guid, size))
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_deploy.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/deploy.py tests/test_gdl_deploy.py
git commit -m "refactor: return preview bytes so a caller need not write a file

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The tools module and the two read-only tools

**Files:**
- Create: `src/archicad_mcp/gdl/tools.py`
- Test: `tests/test_gdl_tools.py` (create)

**Interfaces:**
- Consumes: `Workspace`, `WorkspaceError` (Task 5); `mesh_mod.load`; `cfg_mod.load_config`
- Produces:
  - `register(mcp, default_port, workspace, tool_meta, guarded) -> None`
  - `_list_sources(ws: Workspace) -> dict`
  - `_inspect_source(ws: Workspace, source: str) -> dict`

The two underscore-prefixed functions hold all the logic and take a `Workspace` directly, so tests never need a FastMCP instance. `register` is a thin binding layer. `tool_meta` and `guarded` are passed in rather than imported from `server`, because `server` imports this module and the reverse import would be circular.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdl_tools.py`:

```python
"""GDL tool logic, exercised without a FastMCP instance or Archicad."""

import json

import pytest

from archicad_mcp.gdl import tools as gdl_tools
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError

CUBE_OBJ = """\
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
usemtl steel
f 1 4 3 2
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "cube.obj").write_text(CUBE_OBJ)
    (tmp_path / "textures").mkdir()
    (tmp_path / "textures" / "oak_ab12cd.jpg").write_bytes(b"jpeg")
    (tmp_path / "Chair.gsm").write_bytes(b"gsm")
    (tmp_path / "assets.json").write_text(
        json.dumps({"objects": {"Chair": {"groups": {}}}}))
    return Workspace(tmp_path)


def test_list_sources_groups_by_kind(ws):
    out = gdl_tools._list_sources(ws)
    assert [s["name"] for s in out["sources"]] == ["cube.obj"]
    assert [g["name"] for g in out["built"]] == ["Chair.gsm"]
    assert out["configured_objects"] == ["Chair"]
    assert [t["name"] for t in out["textures"]] == ["oak_ab12cd.jpg"]


def test_list_sources_reports_size_and_mtime(ws):
    entry = gdl_tools._list_sources(ws)["sources"][0]
    assert entry["bytes"] == len(CUBE_OBJ)
    assert "modified" in entry


def test_list_sources_on_a_missing_root(tmp_path):
    with pytest.raises(WorkspaceError, match="does not exist"):
        gdl_tools._list_sources(Workspace(tmp_path / "nope"))


def test_inspect_returns_groups_and_bbox(ws):
    out = gdl_tools._inspect_source(ws, "cube.obj")
    assert out["face_count"] == 6
    assert out["vertex_count"] == 8
    assert [g["material"] for g in out["groups"]] == ["steel"]
    assert out["groups"][0]["faces"] == 6
    assert out["bbox"]["x"] == [0.0, 1.0]


def test_inspect_refuses_a_path_outside_the_workspace(ws):
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._inspect_source(ws, "../cube.obj")


def test_inspect_reports_a_missing_file_plainly(ws):
    with pytest.raises(FileNotFoundError):
        gdl_tools._inspect_source(ws, "absent.obj")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archicad_mcp.gdl.tools'`.

- [ ] **Step 3: Implement the module and the two read tools**

Create `src/archicad_mcp/gdl/tools.py`:

```python
"""MCP tools over the GDL pipeline.

The pipeline is otherwise a CLI, which makes it unreachable from any client
whose agent has no shell on the machine running Archicad. These tools move
execution into the server process, which already runs there by necessity
because it talks to Archicad over localhost.

The logic lives in module-level functions taking a Workspace, and `register`
only binds them to FastMCP. That keeps the tests free of a server instance,
and keeps this module importable from `server` without a circular import
(hence tool_meta and guarded arriving as arguments).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from archicad_mcp.gdl import config as cfg_mod
from archicad_mcp.gdl import mesh as mesh_mod
from archicad_mcp.gdl.workspace import Workspace

SOURCE_SUFFIXES = (".obj", ".3ds")
TEXTURE_SUFFIXES = (".jpg", ".jpeg", ".png")


def _entry(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                            .isoformat(timespec="seconds"),
    }


def _list_sources(ws: Workspace) -> dict:
    root = ws.require_root()
    sources = [_entry(p) for p in sorted(root.iterdir())
               if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES]
    built = [_entry(p) for p in sorted(root.glob("*.gsm"))]
    tex_dir = ws.textures_dir()
    textures = ([_entry(p) for p in sorted(tex_dir.iterdir())
                 if p.is_file() and p.suffix.lower() in TEXTURE_SUFFIXES]
                if tex_dir.is_dir() else [])
    assets = ws.assets_path()
    configured = sorted(cfg_mod.load_config(assets)) if assets.is_file() else []
    return {
        "workspace": str(root),
        "sources": sources,
        "built": built,
        "textures": textures,
        "configured_objects": configured,
    }


def _inspect_source(ws: Workspace, source: str) -> dict:
    path = ws.resolve(source)
    if not path.is_file():
        raise FileNotFoundError(
            f"No such file in the GDL workspace: {source}. Call "
            "list_gdl_sources to see what is there.")
    mesh = mesh_mod.load(path)
    xs = [v[0] for v in mesh.verts]
    ys = [v[1] for v in mesh.verts]
    zs = [v[2] for v in mesh.verts]
    groups = []
    for mat, faces in sorted(mesh.groups.items(), key=lambda kv: -len(kv[1])):
        vs = {vi for f in faces for vi, _ in f}
        groups.append({
            "material": mat,
            "faces": len(faces),
            "x": [round(min(mesh.verts[v][0] for v in vs), 4),
                  round(max(mesh.verts[v][0] for v in vs), 4)],
            "z": [round(min(mesh.verts[v][2] for v in vs), 4),
                  round(max(mesh.verts[v][2] for v in vs), 4)],
        })
    return {
        "source": source,
        "vertex_count": len(mesh.verts),
        "face_count": mesh.face_count,
        "has_uvs": bool(mesh.uvs),
        "bbox": {"x": [round(min(xs), 4), round(max(xs), 4)],
                 "y": [round(min(ys), 4), round(max(ys), 4)],
                 "z": [round(min(zs), 4), round(max(zs), 4)]},
        "groups": groups,
        "notes": list(mesh.notes),
    }


def register(mcp, default_port, workspace: Workspace, tool_meta, guarded) -> None:
    ws = workspace

    @mcp.tool(description="List what is in the GDL workspace folder: source "
                          "meshes (.obj/.3ds), built .gsm library parts, "
                          "textures, and the objects already described in "
                          "assets.json. Call this first.",
              **tool_meta("List GDL workspace", read_only=True, destructive=False))
    @guarded
    def list_gdl_sources() -> dict:
        return _list_sources(ws)

    @mcp.tool(description="Parse a source mesh in the GDL workspace and return "
                          "its material groups with face counts, bounding box, "
                          "and parser notes. Read this before writing a config: "
                          "the group names are what a config's 'groups' keys "
                          "match against. Does not need Archicad running.",
              **tool_meta("Inspect GDL source mesh", read_only=True, destructive=False))
    @guarded
    def inspect_gdl_source(source: str) -> dict:
        return _inspect_source(ws, source)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_tools.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/tools.py tests/test_gdl_tools.py
git commit -m "feat: list_gdl_sources and inspect_gdl_source

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: build_gdl_object

**Files:**
- Modify: `src/archicad_mcp/gdl/tools.py`
- Test: `tests/test_gdl_tools.py` (append)

**Interfaces:**
- Consumes: `cfg_mod.parse_objects`, `cfg_mod.save_object_config`, `cfg_mod.find_object` (Task 4); `mesh_mod.load`; `toolchain.decimate`, `toolchain.compile_hsf`, `toolchain.validate_gsm`; `generate.build_hsf`
- Produces: `_build_object(ws, source, name, config, decimate, validate, save_config) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdl_tools.py`:

```python
def _fake_toolchain(monkeypatch):
    """compile_hsf writes a stub .gsm; validate_gsm reports nothing.

    LP_XMLConverter is not available in CI, and this test is about the tool's
    file handling rather than about the compiler.
    """
    def compile_hsf(hsf_dir, gsm_path):
        from pathlib import Path
        gsm = Path(gsm_path)
        gsm.write_bytes(b"stub gsm")
        return gsm

    monkeypatch.setattr(gdl_tools.toolchain, "compile_hsf", compile_hsf)
    monkeypatch.setattr(gdl_tools.toolchain, "validate_gsm", lambda *a, **k: [])


def test_build_writes_a_gsm_into_the_workspace(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    out = gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                                  decimate=True, validate=True, save_config=True)
    assert (ws.root / "Cube.gsm").is_file()
    assert out["gsm"] == "Cube.gsm"
    assert out["bytes"] == len(b"stub gsm")
    assert out["validation"] == []


def test_build_persists_the_config(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {"steel": {"label": "Frame", "rgb": [0.5, 0.5, 0.5]}}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=True, validate=True, save_config=True)
    saved = json.loads((ws.root / "assets.json").read_text())
    assert saved["objects"]["Cube"] == spec
    assert set(saved["objects"]) == {"Chair", "Cube"}


def test_build_without_save_leaves_assets_alone(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    before = (ws.root / "assets.json").read_text()
    gdl_tools._build_object(ws, "cube.obj", "Cube", config={"groups": {}},
                            decimate=True, validate=True, save_config=False)
    assert (ws.root / "assets.json").read_text() == before


def test_build_falls_back_to_the_saved_config(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    spec = {"groups": {"steel": {"label": "Frame", "rgb": [0.5, 0.5, 0.5]}}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=True, validate=True, save_config=True)
    out = gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                                  decimate=True, validate=True, save_config=True)
    assert "Frame" in " ".join(out["groups"])


def test_build_refuses_a_name_that_is_not_a_filename(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._build_object(ws, "cube.obj", "../Cube", config=None,
                                decimate=True, validate=True, save_config=False)


def test_build_removes_the_intermediate_hsf_folder(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=None,
                            decimate=True, validate=True, save_config=False)
    assert not (ws.root / "Cube").exists()


def test_build_skips_decimation_when_asked(ws, monkeypatch):
    _fake_toolchain(monkeypatch)
    called = []
    monkeypatch.setattr(gdl_tools.toolchain, "decimate",
                        lambda m, t: called.append(t) or m)
    spec = {"groups": {}, "decimate": {"steel": 100}}
    gdl_tools._build_object(ws, "cube.obj", "Cube", config=spec,
                            decimate=False, validate=True, save_config=False)
    assert called == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_tools.py -k build -v`
Expected: FAIL with `AttributeError: module 'archicad_mcp.gdl.tools' has no attribute '_build_object'`.

- [ ] **Step 3: Implement the build**

In `src/archicad_mcp/gdl/tools.py`, extend the imports:

```python
import shutil

from archicad_mcp.gdl import generate, toolchain
```

and add:

```python
def _config_for(ws: Workspace, name: str, config: dict | None):
    """The ObjectConfig to build with, from the argument or from assets.json.

    An inline config is parsed against the workspace root, so its texture and
    source paths are workspace-relative like every other path argument.
    """
    if config is not None:
        objects = cfg_mod.parse_objects({"objects": {name: config}}, ws.root)
        return objects[name], config
    assets = ws.assets_path()
    objects = cfg_mod.load_config(assets) if assets.is_file() else {}
    return cfg_mod.find_object(objects, name), None


def _build_object(ws: Workspace, source: str, name: str,
                  config: dict | None = None, decimate: bool = True,
                  validate: bool = True, save_config: bool = True) -> dict:
    ws.require_root()
    src = ws.resolve(source)
    if not src.is_file():
        raise FileNotFoundError(
            f"No such file in the GDL workspace: {source}. Call "
            "list_gdl_sources to see what is there.")
    gsm_path = ws.resolve(f"{name}.gsm")
    hsf_dir = ws.resolve(name)

    cfg, raw_spec = _config_for(ws, name, config)
    mesh = mesh_mod.load(src)
    notes = list(mesh.notes)
    if cfg.decimate and decimate:
        seen = len(mesh.notes)
        mesh = toolchain.decimate(mesh, cfg.decimate)
        notes += mesh.notes[seen:]

    if hsf_dir.exists():
        shutil.rmtree(hsf_dir)
    result = generate.build_hsf(mesh, cfg, name, hsf_dir,
                                textures_dir=ws.textures_dir())
    try:
        gsm = toolchain.compile_hsf(hsf_dir, gsm_path)
    finally:
        shutil.rmtree(hsf_dir, ignore_errors=True)

    findings = (toolchain.validate_gsm(gsm, extra_libs=[ws.textures_dir()])
                if validate else [])
    if save_config and raw_spec is not None:
        cfg_mod.save_object_config(ws.assets_path(), name, raw_spec)

    return {
        "gsm": gsm.name,
        "bytes": gsm.stat().st_size,
        "guid": result.guid,
        "size_m": {"a": round(result.a, 4), "b": round(result.b, 4),
                   "h": round(result.h, 4)},
        "groups": list(result.groups),
        "textures": [p.name for p in result.textures],
        "notes": notes + list(result.notes),
        "validation": [ln.strip() for ln in findings],
        "config_saved": bool(save_config and raw_spec is not None),
    }
```

Then register it inside `register`:

```python
    @mcp.tool(description="Build a .gsm library part from a source mesh in the "
                          "GDL workspace. Pass 'config' to describe the object "
                          "(groups, textures, variants, decimate targets); it is "
                          "saved into assets.json on success, so a later rebuild "
                          "only needs the name. Writes <name>.gsm and textures/ "
                          "into the workspace, which is the linked library "
                          "folder. Does not need Archicad running. A clean "
                          "validation does NOT prove the geometry survived: "
                          "deploy and look at the render.",
              **tool_meta("Build GDL library part", read_only=False, destructive=True))
    @guarded
    def build_gdl_object(source: str, name: str, config: dict | None = None,
                         decimate: bool = True, validate: bool = True,
                         save_config: bool = True) -> dict:
        return _build_object(ws, source, name, config, decimate, validate,
                             save_config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_tools.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/gdl/tools.py tests/test_gdl_tools.py
git commit -m "feat: build_gdl_object, config persisted on success

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: deploy_gdl_object with the transient probe render

The render is the only automated gate that catches a silently dropped body, and a sandboxed agent can only use it if the image comes back inline. `keep=False` places, renders, then deletes, so an iteration loop does not stack chairs at the origin.

**Files:**
- Modify: `src/archicad_mcp/gdl/tools.py`
- Test: `tests/test_gdl_tools.py` (append)

**Interfaces:**
- Consumes: `deploy_mod.reload_libraries`, `place_object`, `preview_image_bytes` (Task 6), `embed_gsm`, `embed_textures`; `archicad_mcp.core.mutate.delete_elements`
- Produces: `_deploy_object(ws, conn, name, place, keep, embed) -> tuple[dict, bytes]`

`_deploy_object` returns the payload and the PNG bytes separately, so the tool wrapper owns the FastMCP `Image` type and the tests do not have to.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdl_tools.py`:

```python
import base64

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeConn:
    port = 19723

    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append((command, params))
        if command == "GetElementPreviewImage":
            return {"previewImage": base64.b64encode(PNG).decode()}
        if command == "CreateObjects":
            return {"elements": [{"elementId": {"guid": "ABC-123"}}]}
        return {}


def _commands(conn):
    return [c for c, _ in conn.calls]


def test_deploy_reloads_places_renders_and_deletes(ws):
    conn = FakeConn()
    payload, png = gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0),
                                            keep=False, embed=False)
    assert _commands(conn) == ["ReloadLibraries", "CreateObjects",
                               "GetElementPreviewImage", "DeleteElements"]
    assert png == PNG
    assert payload["kept"] is False
    assert payload["element_guid"] == "ABC-123"


def test_deploy_with_keep_does_not_delete(ws):
    conn = FakeConn()
    payload, _ = gdl_tools._deploy_object(ws, conn, "Chair", place=(1.0, 2.0),
                                          keep=True, embed=False)
    assert "DeleteElements" not in _commands(conn)
    assert payload["kept"] is True


def test_deploy_places_at_the_requested_coordinates(ws):
    conn = FakeConn()
    gdl_tools._deploy_object(ws, conn, "Chair", place=(1.5, -2.5), keep=True,
                             embed=False)
    params = dict(conn.calls)["CreateObjects"]
    assert params["objectsData"][0]["coordinates"] == {"x": 1.5, "y": -2.5, "z": 0.0}
    assert params["objectsData"][0]["libraryPartName"] == "Chair"


def test_deploy_embeds_when_asked(ws):
    conn = FakeConn()
    gdl_tools._deploy_object(ws, conn, "Chair", place=(0.0, 0.0), keep=False,
                             embed=True)
    assert _commands(conn)[0] == "AddFilesToEmbeddedLibrary"


def test_deploy_refuses_a_missing_gsm(ws):
    conn = FakeConn()
    with pytest.raises(FileNotFoundError, match="Absent.gsm"):
        gdl_tools._deploy_object(ws, conn, "Absent", place=(0.0, 0.0),
                                 keep=False, embed=False)


def test_deploy_refuses_a_name_outside_the_workspace(ws):
    conn = FakeConn()
    with pytest.raises(WorkspaceError, match="outside"):
        gdl_tools._deploy_object(ws, conn, "../Chair", place=(0.0, 0.0),
                                 keep=False, embed=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdl_tools.py -k deploy -v`
Expected: FAIL with `AttributeError: module 'archicad_mcp.gdl.tools' has no attribute '_deploy_object'`.

- [ ] **Step 3: Implement the deploy**

In `src/archicad_mcp/gdl/tools.py`, extend the imports:

```python
from archicad_mcp.core import mutate as _mutate
from archicad_mcp.gdl import deploy as deploy_mod
```

and add:

```python
def _deploy_object(ws: Workspace, conn, name: str, place: tuple[float, float],
                   keep: bool, embed: bool) -> tuple[dict, bytes]:
    """Reload, place, render, and unless kept, delete again.

    The render is the only automated check that catches a body Archicad
    silently dropped, so it is not optional. But an iteration loop that leaves
    every attempt standing at the origin is unusable, so the default is a
    transient probe: the element created here is deleted here, leaving the
    project net-zero.
    """
    ws.require_root()
    gsm = ws.resolve(f"{name}.gsm")
    if not gsm.is_file():
        raise FileNotFoundError(
            f"No such library part in the GDL workspace: {name}.gsm. Build it "
            "first with build_gdl_object.")

    embedded = None
    if embed:
        tex_dir = ws.textures_dir()
        textures = ([p for p in sorted(tex_dir.iterdir())
                     if p.suffix.lower() in TEXTURE_SUFFIXES]
                    if tex_dir.is_dir() else [])
        deploy_mod.embed_gsm(conn, gsm)
        added, skipped = deploy_mod.embed_textures(conn, textures)
        embedded = {"textures_added": added, "textures_skipped": skipped}

    deploy_mod.reload_libraries(conn)
    x, y = place
    guid = deploy_mod.place_object(conn, name, x=x, y=y)
    png = deploy_mod.preview_image_bytes(conn, guid)
    if not keep:
        _mutate.delete_elements(conn, [guid], confirm=True)

    payload = {
        "library_part": name,
        "element_guid": guid,
        "placed_at": {"x": x, "y": y},
        "kept": keep,
        "port": conn.port,
        "note": ("Look at the render. A body Archicad dropped shows up here and "
                 "nowhere else: the offline validator passes it."),
    }
    if embedded is not None:
        payload["embedded"] = embedded
    return payload, png
```

Note the internal delete passes `confirm=True` without asking. It removes only the element this same call created moments earlier, so it is cleanup rather than a user-data deletion, and the tool would be useless if every probe needed a confirmation round trip. `delete_elements` on the server stays confirmation-gated for everything else.

Then register it inside `register`, converting the bytes to MCP image content:

```python
    @mcp.tool(description="Reload libraries, place the built library part, "
                          "render it, and return the image. The render is the "
                          "only check that catches a 3D body Archicad silently "
                          "dropped, so look at it. By default the placed "
                          "instance is deleted again after the render, leaving "
                          "the project unchanged; pass keep=true to actually "
                          "place it. Requires Archicad running with a project "
                          "open, and the workspace folder added once as a "
                          "linked library via Library Manager.",
              **tool_meta("Deploy GDL library part", read_only=False, destructive=True))
    @guarded
    def deploy_gdl_object(name: str, x: float = 0.0, y: float = 0.0,
                          keep: bool = False, embed: bool = False,
                          port: int | None = None) -> list:
        from fastmcp.utilities.types import Image

        conn = get_connection(port if port is not None else default_port)
        payload, png = _deploy_object(ws, conn, name, (x, y), keep, embed)
        return [payload, Image(data=png, format="png")]
```

Add `from archicad_mcp.connection import get_connection` to the module imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdl_tools.py -v`
Expected: PASS, 19 tests.

- [ ] **Step 5: Verify the image content type actually serialises**

Run:

```bash
uv run python -c "
from fastmcp.utilities.types import Image
img = Image(data=b'\x89PNG\r\n\x1a\nfake', format='png')
print(type(img.to_image_content()).__name__)
"
```

Expected: prints `ImageContent`. If the helper is named differently in fastmcp 3.4.4, adjust the return shape and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/gdl/tools.py tests/test_gdl_tools.py
git commit -m "feat: deploy_gdl_object returns the verification render inline

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Wire the tools into the server

**Files:**
- Modify: `src/archicad_mcp/server.py` (imports, `build_server`, `format_startup_banner`, `main`)
- Test: `tests/test_server_gdl_wiring.py` (create)

**Interfaces:**
- Consumes: `gdl_tools.register` (Task 7), `Workspace` (Task 5)
- Produces: `resolve_gdl_workspace(raw) -> Path | None`; `build_server(mode, rules_dir, port, gdl_workspace)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_gdl_wiring.py`:

```python
"""GDL tools register only in full mode, and only with a workspace configured."""

from pathlib import Path

from archicad_mcp.server import build_server, resolve_gdl_workspace

GDL_TOOLS = {"list_gdl_sources", "inspect_gdl_source", "build_gdl_object",
             "deploy_gdl_object"}


async def _tool_names(server):
    return set(await server.get_tools())


def test_resolve_treats_blank_as_unset():
    assert resolve_gdl_workspace("") is None
    assert resolve_gdl_workspace("   ") is None
    assert resolve_gdl_workspace(None) is None


def test_resolve_returns_a_path():
    assert resolve_gdl_workspace("/tmp/gdl") == Path("/tmp/gdl")


async def test_no_workspace_means_no_gdl_tools():
    server = build_server(mode="full", gdl_workspace=None)
    assert not (GDL_TOOLS & await _tool_names(server))


async def test_full_mode_with_workspace_registers_them(tmp_path):
    server = build_server(mode="full", gdl_workspace=tmp_path)
    assert GDL_TOOLS <= await _tool_names(server)


async def test_verdicts_mode_never_registers_them(tmp_path):
    server = build_server(mode="verdicts", gdl_workspace=tmp_path)
    assert not (GDL_TOOLS & await _tool_names(server))
```

If `get_tools` is not the accessor on fastmcp 3.4.4, find the right one with `uv run python -c "from fastmcp import FastMCP; print([n for n in dir(FastMCP) if 'tool' in n.lower()])"` and adjust `_tool_names` only.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_gdl_wiring.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_gdl_workspace'`.

- [ ] **Step 3: Wire it up**

In `src/archicad_mcp/server.py`:

Add to `_HANDLED_ERRORS` so workspace and toolchain failures become `{"error": ...}` rather than protocol-level errors:

```python
from archicad_mcp.gdl.toolchain import ToolchainError
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError

_HANDLED_ERRORS = (ArchicadUnavailableError, APIErrorBase, ToolchainError,
                   WorkspaceError, FileNotFoundError)
```

Change the `build_server` signature and add the registration next to the existing full-mode call:

```python
def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
    gdl_workspace: Path | None = None,
) -> FastMCP:
```

```python
    if mode == "full":
        _register_full_mode_tools(mcp, default_port)
        if gdl_workspace is not None:
            from archicad_mcp.gdl import tools as gdl_tools
            gdl_tools.register(mcp, default_port, Workspace(gdl_workspace),
                               _tool_meta, _guarded)
    mcp.archicad_gdl_workspace = gdl_workspace

    return mcp
```

The `gdl` import stays inside the branch so a verdicts-mode server never imports Pillow or the mesh code.

Add a banner line. In `format_startup_banner`, add a `gdl_workspace` parameter defaulting to `None` and append to `head`:

```python
    if gdl_workspace is not None:
        head += f", GDL workspace {gdl_workspace}"
    else:
        head += ", GDL tools off (no workspace folder set)"
```

Thread it through `emit_startup_banner(mode, rule_count, rule_errors=0, gdl_workspace=None)` and its `format_startup_banner` call, and through the discovery-failure fallback line.

In `main`, add the argument and the env var:

```python
    parser.add_argument("--gdl-workspace", type=Path,
                        default=resolve_gdl_workspace(
                            os.environ.get("ARCHICAD_MCP_GDL_WORKSPACE")))
```

```python
    gdl_workspace = resolve_gdl_workspace(args.gdl_workspace)
    server = build_server(mode=args.mode, rules_dir=rules_dir, port=args.port,
                          gdl_workspace=gdl_workspace)
    emit_startup_banner(args.mode, server.archicad_rule_count,
                        server.archicad_rule_errors, gdl_workspace)
```

And add the resolver next to `resolve_rules_dir`:

```python
def resolve_gdl_workspace(raw: str | Path | None) -> Path | None:
    """None for an unset or blank workspace, so the GDL tools stay unregistered.

    Same trap as resolve_rules_dir: an unfilled .mcpb field arrives as an empty
    string, and Path("") is Path("."), which is truthy. Registering the GDL
    tools against the working directory would be worse than not registering
    them, because builds would write .gsm files into it.
    """
    text = str(raw).strip() if raw is not None else ""
    return Path(text) if text else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_gdl_wiring.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Existing banner tests will need the new `gdl_workspace` text accounted for; update their expected strings rather than making the parameter optional in the output.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/server.py tests/test_server_gdl_wiring.py tests/
git commit -m "feat: register the GDL tools when a workspace folder is set

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Extension manifest, docs, and a live check

**Files:**
- Modify: `manifest.json` (`user_config`, `tools`)
- Modify: `docs/gdl-pipeline.md`
- Modify: `README.md`
- Test: `tests/test_manifest.py` (create or extend if one exists; check with `ls tests/`)

**Interfaces:**
- Consumes: everything above
- Produces: a bundle that exposes the four tools

- [ ] **Step 1: Write the failing test**

The manifest's `tools` array is `tools_generated: true` metadata that clients show before install, so a tool missing from it is invisible in the install dialog. Create `tests/test_manifest.py`:

```python
"""The manifest's advertised tool list must match what the server registers."""

import json
from pathlib import Path

from archicad_mcp.server import build_server

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


async def test_manifest_lists_every_registered_tool(tmp_path):
    raw = json.loads(MANIFEST.read_text())
    advertised = {t["name"] for t in raw["tools"]}
    server = build_server(mode="full", gdl_workspace=tmp_path)
    assert set(await server.get_tools()) == advertised


def test_manifest_declares_the_gdl_workspace_field():
    raw = json.loads(MANIFEST.read_text())
    field = raw["user_config"]["gdl_workspace"]
    assert field["type"] == "directory"
    assert raw["server"]["mcp_config"]["env"]["ARCHICAD_MCP_GDL_WORKSPACE"] == \
        "${user_config.gdl_workspace}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_manifest.py -v`
Expected: FAIL with `KeyError: 'gdl_workspace'`.

- [ ] **Step 3: Update the manifest**

Add to `user_config`:

```json
    "gdl_workspace": {
      "type": "directory",
      "title": "GDL workspace folder",
      "description": "Folder holding source meshes, textures, assets.json and the .gsm library parts this server builds. Add the same folder to Archicad once via Library Manager so rebuilds update placed objects in place. Leave empty to switch the GDL tools off.",
      "multiple": false,
      "required": false
    }
```

Add to `server.mcp_config.env`:

```json
        "ARCHICAD_MCP_GDL_WORKSPACE": "${user_config.gdl_workspace}"
```

Add to `tools`:

```json
    {
      "name": "list_gdl_sources",
      "description": "List source meshes, built library parts, and textures in the GDL workspace."
    },
    {
      "name": "inspect_gdl_source",
      "description": "Parse a source mesh and report its material groups and bounding box."
    },
    {
      "name": "build_gdl_object",
      "description": "Build a .gsm library part from a source mesh. Writes files."
    },
    {
      "name": "deploy_gdl_object",
      "description": "Reload libraries, place the object, and return a render of it."
    }
```

Bump `version` in both `manifest.json` and `pyproject.toml` to `0.3.0`. The release workflow refuses to publish unless the tag, `pyproject.toml` and `manifest.json` all state the same version.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Document the MCP route**

Add a section to `docs/gdl-pipeline.md` after the CLI usage block:

```markdown
## From an MCP client

The CLI needs a shell on the machine running Archicad. Clients whose agent runs
elsewhere (Cowork runs it in a Linux sandbox) reach the same pipeline through
four tools instead, which execute inside the server process:

`list_gdl_sources`, `inspect_gdl_source`, `build_gdl_object`,
`deploy_gdl_object`.

Set the **GDL workspace folder** in the extension settings, and add that same
folder to Archicad once via File > Libraries and Objects > Library Manager.
Source meshes and texture files go in it by hand; everything else the tools
write lands there too. Without the field set, the tools do not register at all
and the startup banner says so.

`deploy_gdl_object` deletes the instance it placed once it has rendered it, so
repeated builds do not stack objects at the origin. Pass `keep=true` when you
want the object left in the project.
```

Add a line to the README's tool table or list matching its existing format.

- [ ] **Step 6: Live check**

Ask the user for a scratch project and port first. With the workspace folder set and added as a linked library:

1. `list_gdl_sources` shows the source dropped in by hand.
2. `inspect_gdl_source` returns its material groups.
3. `build_gdl_object` with a config produces a `.gsm` and writes `assets.json`.
4. `deploy_gdl_object` returns a render that actually shows the object.
5. Project element count is unchanged afterwards.

If the render comes back empty or missing bodies, that is the pipeline's known failure mode and not a wiring bug: see the manifold edge-splitting note in `docs/gdl-pipeline.md`.

- [ ] **Step 7: Commit**

```bash
git add manifest.json pyproject.toml docs/gdl-pipeline.md README.md tests/test_manifest.py
git commit -m "feat: ship the GDL tools in the extension bundle

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review notes

Spec coverage checked section by section: architecture and boundaries (Tasks 5, 7, 10), the gating probe (Task 1), all four tools (Tasks 7, 8, 9), config flow (Tasks 4, 8), workspace containment (Task 5), Windows portability (Tasks 2, 3), errors (Task 10's `_HANDLED_ERRORS`), testing (each task), and the manifest field (Task 11).

Two places where the plan tells the implementer to verify a library detail rather than trust this document: the fastmcp `Image` accessor in Task 9 Step 5, and `FastMCP.get_tools` in Task 10 Step 1. Both are named so a mismatch is a one-line fix rather than a stall.
