import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastmcp import Client

import archicad_mcp.core.schemes as core_schemes
from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.schemes import edit_schedule_scheme, read_schedule_scheme
from archicad_mcp.schemes.columns import ColumnNotFound, DuplicateColumnCaption
from archicad_mcp.server import build_server
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

SPEC_YAML = """
- id: door-schedule
  template: sample_scheme.xml
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
"""


def setup_case(tmp_path):
    scheme = tmp_path / "sample_scheme.xml"
    shutil.copy(FIXTURE, scheme)
    spec = tmp_path / "schemes.yaml"
    spec.write_text(SPEC_YAML, encoding="utf-8")
    return scheme, spec


def test_dry_run_writes_nothing(tmp_path):
    scheme, spec = setup_case(tmp_path)
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["dry_run"] is True
    assert out["written"] is None
    assert scheme.read_bytes() == before


def test_dry_run_reports_before_and_after(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["columns_before"] == ["Door ID", "Quantity", "Fire Resistance"]
    assert out["columns_after"] == ["Quantity", "Door ID"]
    assert any("Fire Resistance" in c for c in out["changes"])


def test_commit_writes_to_the_output_path(tmp_path):
    scheme, spec = setup_case(tmp_path)
    dest = tmp_path / "edited.xml"
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert out["written"] == str(dest)
    assert dest.is_file()
    assert read_schedule_scheme(str(dest))["columns"][0]["caption"] == "Quantity"


def test_commit_never_overwrites_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    before = scheme.read_bytes()
    edit_schedule_scheme(str(scheme), str(spec), output=str(tmp_path / "e.xml"),
                         dry_run=False)
    assert scheme.read_bytes() == before


def test_commit_refuses_to_write_over_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(scheme), dry_run=False)
    assert "error" in out
    assert "overwrite" in out["error"].lower()


# --- The overwrite guard used to compare dest.resolve() == source.resolve(),
# which is text comparison, not identity comparison. Two paths that name the
# same file without being spelled identically slip past it, and the tool
# reports success while silently overwriting the input. samefile() compares
# device and inode instead, which is alias-aware. ---

def _filesystem_is_case_sensitive(directory):
    """True when directory's filesystem treats two names differing only by
    case as different files.

    Case sensitivity is a property of the filesystem, not the OS (an exFAT
    drive mounted on Linux is still case-insensitive, and ubuntu-latest's
    default ext4 is case-sensitive), so this is checked at runtime in the
    same directory a test will use rather than guessed from sys.platform.
    """
    probe = directory / "case_probe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        aliased = probe.with_name(probe.name.upper())
        return not aliased.exists()
    finally:
        probe.unlink()


def test_commit_refuses_an_output_that_differs_from_the_input_only_by_case(tmp_path):
    """On a case-insensitive filesystem (macOS's default APFS, which this
    repo runs its tests on), SAMPLE_SCHEME.xml and sample_scheme.xml name the
    exact same file. dest.resolve() == source.resolve() compares the path
    text, sees two different strings, and lets the write through, silently
    destroying the input. This must be refused exactly like passing the
    input path back unchanged.

    Skipped on a case-sensitive filesystem (CI runs ubuntu-latest, whose
    default ext4 is case-sensitive), where SAMPLE_SCHEME.xml and
    sample_scheme.xml are just two different, unrelated files and the
    aliasing this test is about cannot happen."""
    if _filesystem_is_case_sensitive(tmp_path):
        pytest.skip("requires a case-insensitive filesystem")
    scheme, spec = setup_case(tmp_path)
    alias = scheme.with_name("SAMPLE_SCHEME.xml")
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(alias), dry_run=False)
    assert "error" in out
    assert "overwrite" in out["error"].lower()
    assert scheme.read_bytes() == before


@pytest.mark.skipif(not hasattr(os, "link"),
                    reason="os.link is not available on this platform")
def test_commit_refuses_a_hard_link_to_the_input(tmp_path):
    """A hard link is a second directory entry for the same inode: same
    file, different, unrelated-looking path. dest.resolve() == source.resolve()
    sees two unrelated paths and lets the write through."""
    scheme, spec = setup_case(tmp_path)
    linked = tmp_path / "linked.xml"
    os.link(str(scheme), str(linked))
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(linked), dry_run=False)
    assert "error" in out
    assert "overwrite" in out["error"].lower()
    assert scheme.read_bytes() == before


def test_commit_falls_back_when_the_overwrite_guard_cannot_stat_the_output(
        tmp_path, monkeypatch):
    """samefile() has to stat both paths, and the guard used to catch only
    FileNotFoundError from that, on the assumption that a missing dest (the
    common case: a fresh write) is the only way the stat can fail. A dest
    whose directory exists but cannot be searched makes stat() raise
    PermissionError instead, which is a sibling of FileNotFoundError under
    OSError, not a subclass of it, so it escaped uncaught. This tool is
    registered without @_guarded, so nothing else would have caught it,
    breaking the "always returns a dict" contract.

    Patched directly here, rather than through a real unsearchable
    directory, both because the fix is about the except clause's type, not
    about any particular OSError subtype, and because POSIX permission bits
    do not translate to Windows, which is part of this project's CI matrix."""
    scheme, spec = setup_case(tmp_path)
    dest = tmp_path / "out.xml"

    def boom(self, other):
        raise PermissionError("simulated: cannot stat this path")

    monkeypatch.setattr(Path, "samefile", boom)
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert "error" not in out
    assert out["written"] == str(dest)
    assert dest.is_file()


def test_commit_without_output_defaults_beside_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), dry_run=False)
    assert out["written"].endswith("sample_scheme.edited.xml")
    assert Path(out["written"]).is_file()


def test_unknown_spec_id_is_an_error(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), spec_id="nope")
    assert "error" in out
    assert "door-schedule" in out["error"]


def test_spec_load_errors_are_surfaced(tmp_path):
    scheme, _ = setup_case(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("- template: t.xml\n", encoding="utf-8")
    out = edit_schedule_scheme(str(scheme), str(bad))
    assert "error" in out


def test_matching_template_produces_no_warning(tmp_path):
    scheme, spec = setup_case(tmp_path)
    assert edit_schedule_scheme(str(scheme), str(spec))["warnings"] == []


def test_template_mismatch_warns_but_still_applies(tmp_path):
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "window.yaml"
    spec.write_text(SPEC_YAML.replace("sample_scheme.xml", "window_scheme.xml"),
                    encoding="utf-8")
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["warnings"] and "window_scheme.xml" in out["warnings"][0]
    assert out["columns_after"] == ["Quantity", "Door ID"]


def test_refuses_a_scheme_it_would_rewrite_on_save(tmp_path):
    _, spec = setup_case(tmp_path)
    # An explicit <Tag></Tag> pair is a construct the serializer would collapse.
    weird = tmp_path / "weird.xml"
    weird.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        "<DimensionSetting value=\"0\"/>",
        "<DimensionSetting value=\"0\"></DimensionSetting>", 1), encoding="utf-8")
    out = edit_schedule_scheme(str(weird), str(spec))
    assert "error" in out
    assert "corrupt" in out["error"].lower()


# --- Finding 2: round_trips_exactly reads the file as UTF-8 to compare it
# against the re-serialised output. A scheme that declares, and is actually
# written in, a different encoding raises UnicodeDecodeError there, which is
# not an OSError, so it used to escape edit_schedule_scheme uncaught,
# breaking the "always returns a dict" contract read_schedule_scheme already
# upholds for the exact same file (read_schedule_scheme never calls
# round_trips_exactly at all, so it was never exposed to this). ---

def _as_latin1_scheme(path, extra_caption_suffix=" \xe9"):
    """Rewrite the fixture to declare, and actually be written in,
    ISO-8859-1, with a non-ASCII byte (0xE9) in the scheme name so the file
    is not valid UTF-8. Written straight to `path`, which setup_case already
    pointed at a copy of the fixture."""
    text = FIXTURE.read_text(encoding="utf-8")
    text = text.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"')
    text = text.replace("Sample Door Scheme", "Sample Door Scheme" + extra_caption_suffix)
    path.write_bytes(text.encode("ISO-8859-1"))


def test_non_utf8_scheme_is_an_error_envelope_not_a_raw_unicodedecodeerror(tmp_path):
    scheme, spec = setup_case(tmp_path)
    _as_latin1_scheme(scheme)

    out = edit_schedule_scheme(str(scheme), str(spec))

    assert "error" in out
    assert "UTF-8" in out["error"]


def test_read_schedule_scheme_still_handles_the_same_non_utf8_file(tmp_path):
    """Companion pin: read_schedule_scheme never calls round_trips_exactly, so
    this exact file already worked there both before and after the fix. Pinned
    here so the asymmetry finding 2 was about cannot silently regress."""
    scheme, _ = setup_case(tmp_path)
    _as_latin1_scheme(scheme)

    out = read_schedule_scheme(str(scheme))

    assert "error" not in out
    assert out["name"] == "Sample Door Scheme \xe9"


# --- The column and spec layers below apply_spec can raise more than
# SpecError: add_column and rename_column raise DuplicateColumnCaption, and
# _find raises ColumnNotFound. edit_schedule_scheme's contract, like
# read_schedule_scheme's, is to always return a dict, so all three must be
# caught here, not just SpecError. ---

def test_apply_time_spec_error_is_surfaced_not_raised(tmp_path):
    """load_specs only validates a bind's shape at load time. The value
    itself is resolved later by binding_from_bind, from inside apply_spec,
    and can still raise SpecError there, for instance a property given as a
    name rather than a GUID with no resolver available (edit_schedule_scheme
    never passes one). This must come back as an error envelope, exactly
    like a load-time error, not escape as a raw SpecError."""
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "named_property.yaml"
    spec.write_text("""
- id: bad-bind
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
""", encoding="utf-8")
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert "error" in out
    assert scheme.read_bytes() == before


def test_column_not_found_from_the_column_layer_is_caught(tmp_path, monkeypatch):
    """Unreachable through a well-formed spec today, since every _find call
    apply_spec triggers is preceded by a membership check using the exact
    same caption, but ColumnNotFound is a documented exception of the column
    layer apply_spec sits on top of. Patched directly to prove the tool's
    "always returns a dict" contract holds even if that ever changed."""
    scheme, spec = setup_case(tmp_path)

    def boom(*args, **kwargs):
        raise ColumnNotFound("No column captioned 'Nope'. Columns: none.")

    monkeypatch.setattr(core_schemes, "apply_spec", boom)
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert "error" in out
    assert "Nope" in out["error"]


def test_duplicate_column_caption_from_the_column_layer_is_caught(tmp_path, monkeypatch):
    """Same defensive contract as test_column_not_found_from_the_column_layer_is_caught,
    for DuplicateColumnCaption (raised by add_column/rename_column)."""
    scheme, spec = setup_case(tmp_path)

    def boom(*args, **kwargs):
        raise DuplicateColumnCaption("A column captioned 'Quantity' already exists.")

    monkeypatch.setattr(core_schemes, "apply_spec", boom)
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert "error" in out
    assert "Quantity" in out["error"]


# --- save_scheme_tree(scheme.tree, dest) used to be called with no error
# handling. This tool is registered without @_guarded (it never talks to
# Archicad), so nothing else catches an OSError from the write, and it
# escaped as a raw exception instead of the dict this tool always promises.
# Each of these reproduces a distinct OSError save_scheme_tree's write can
# raise: a missing destination directory, a destination that is itself a
# directory, and a destination directory with no write permission. ---

def test_write_failure_nonexistent_directory_is_an_error_envelope(tmp_path):
    scheme, spec = setup_case(tmp_path)
    dest = tmp_path / "does_not_exist" / "out.xml"
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert "error" in out
    assert str(dest) in out["error"]
    assert scheme.read_bytes() == before


def test_write_failure_destination_is_a_directory_is_an_error_envelope(tmp_path):
    scheme, spec = setup_case(tmp_path)
    dest = tmp_path / "already_a_directory"
    dest.mkdir()
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert "error" in out
    assert str(dest) in out["error"]
    assert scheme.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX permission bits do not apply on Windows")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores directory permission bits")
def test_write_failure_read_only_directory_is_an_error_envelope(tmp_path):
    scheme, spec = setup_case(tmp_path)
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    dest = readonly_dir / "out.xml"
    before = scheme.read_bytes()
    readonly_dir.chmod(0o555)
    try:
        out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
        assert "error" in out
        assert str(dest) in out["error"]
        assert scheme.read_bytes() == before
    finally:
        readonly_dir.chmod(0o755)


# --- A "Group/Name" property bind has no GUID: apply_spec needs a resolver
# to turn the name into one, and edit_schedule_scheme used to never build
# one, so the name form never worked, no matter whether Archicad was open.
# The fix: edit_schedule_scheme now inspects the loaded spec, and connects
# only when at least one column binds a property by name rather than by
# GUID. A GUID-only spec (builtins and GDL params included) must stay
# completely offline, which is proven below by making a connection attempt
# fail the test outright. ---

PROPERTIES_RESPONSE = {
    "properties": [
        {"propertyId": {"guid": "69A58F6F-1111-4000-8000-000000000001"},
         "propertyGroupName": "OFFICE", "propertyName": "Door ID"},
    ]
}


def conn_with_properties(properties=PROPERTIES_RESPONSE):
    # conn.tapir() gates on tapir_available(), which probes via the OFFICIAL
    # table, so the fake has to answer that too or every call raises.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                    tapir={"GetAllProperties": properties})
    return ArchicadConnection(19723, core=core)


def test_guid_only_spec_never_connects_to_archicad(tmp_path, monkeypatch):
    """SPEC_YAML binds Quantity as a builtin and Door ID by GUID: no column
    names a property, so no resolver is needed and get_connection must
    never be called. Patched to fail the test if it is reached at all,
    rather than merely asserting no error came back."""
    scheme, spec = setup_case(tmp_path)

    def must_not_be_called(port):
        raise AssertionError(
            "get_connection must not be called for a GUID-only spec")

    monkeypatch.setattr(core_schemes, "get_connection", must_not_be_called)
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert "error" not in out
    assert out["columns_after"] == ["Quantity", "Door ID"]


def test_named_property_resolves_through_a_stubbed_connection(tmp_path, monkeypatch):
    """bind: { property: "OFFICE/Door ID" } names a property instead of
    giving its GUID. edit_schedule_scheme must detect this, connect, and
    build a resolver from property_index so apply_spec can look the name
    up. Proven end to end: write the result and read the GUID back out of
    it, rather than inspecting internals."""
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "named.yaml"
    spec.write_text("""
- id: named
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Door ID"
      bind: { property: "OFFICE/Door ID" }
""", encoding="utf-8")
    calls = []

    def fake_get_connection(port):
        calls.append(port)
        return conn_with_properties()

    monkeypatch.setattr(core_schemes, "get_connection", fake_get_connection)

    dest = tmp_path / "resolved.xml"
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert "error" not in out
    # Proves the resolver path actually ran, rather than the GUID having
    # come from anywhere else by coincidence.
    assert calls, "get_connection must be called to resolve a named property"
    door = next(c for c in read_schedule_scheme(str(dest))["columns"]
               if c["caption"] == "Door ID")
    assert door["detail"] == "69A58F6F-1111-4000-8000-000000000001"


def test_named_property_not_found_is_an_error_naming_it(tmp_path, monkeypatch):
    """The project is open and answering, but it simply has no property by
    this name: the user needs to learn that the property does not exist,
    not get a silent failure or an unrelated stack trace. Checks for the
    resolver's own "not found" wording specifically, not just the property
    name being present anywhere in the message: the old, pre-fix error
    ("...no live model is available to resolve it") also happens to
    mention the property name, so asserting on the name alone would pass
    even without the fix."""
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "named.yaml"
    spec.write_text("""
- id: named
  columns:
    - caption: "Ghost"
      bind: { property: "OFFICE/Nonexistent Property" }
""", encoding="utf-8")
    monkeypatch.setattr(core_schemes, "get_connection",
                        lambda port: conn_with_properties())

    out = edit_schedule_scheme(str(scheme), str(spec))
    assert "error" in out
    assert "OFFICE/Nonexistent Property" in out["error"]
    assert "was not found in the open project" in out["error"]


# --- edit_schedule_scheme's core function has no try/except of its own for
# a failed connection, same contract as validate_schedule_scheme: only
# @_guarded at the tool layer (server.py) turns ArchicadUnavailableError
# into an error envelope. This must be proven through the registered tool,
# not the bare core function, which is why this one test goes through
# fastmcp's Client rather than calling edit_schedule_scheme directly. ---

async def test_tool_converts_missing_archicad_to_an_error_envelope_when_a_name_needs_resolving(
        tmp_path, monkeypatch):
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "named.yaml"
    spec.write_text("""
- id: named
  columns:
    - caption: "Door ID"
      bind: { property: "OFFICE/Door ID" }
""", encoding="utf-8")

    def boom(port):
        raise ArchicadUnavailableError(
            "No running Archicad found. Start Archicad 29 and open a project.")

    monkeypatch.setattr(core_schemes, "get_connection", boom)

    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(
            "edit_schedule_scheme", {"path": str(scheme), "spec_path": str(spec)})
        payload = json.loads(result.content[0].text)
    assert "error" in payload
    assert "No running Archicad" in payload["error"]
