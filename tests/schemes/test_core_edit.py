import os
import shutil
import sys
from pathlib import Path

import pytest

import archicad_mcp.core.schemes as core_schemes
from archicad_mcp.core.schemes import edit_schedule_scheme, read_schedule_scheme
from archicad_mcp.schemes.columns import ColumnNotFound, DuplicateColumnCaption

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

def test_commit_refuses_an_output_that_differs_from_the_input_only_by_case(tmp_path):
    """On a case-insensitive filesystem (macOS's default APFS, which this
    repo runs its tests on), SAMPLE_SCHEME.xml and sample_scheme.xml name the
    exact same file. dest.resolve() == source.resolve() compares the path
    text, sees two different strings, and lets the write through, silently
    destroying the input. This must be refused exactly like passing the
    input path back unchanged."""
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
