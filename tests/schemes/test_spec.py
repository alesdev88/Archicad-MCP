import xml.etree.ElementTree as ET

import pytest

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    Binding,
    field_value,
    same_target,
)
from archicad_mcp.schemes.spec import (
    ColumnSpec,
    SchemeSpec,
    SpecError,
    apply_spec,
    binding_from_bind,
    load_specs,
)
from archicad_mcp.schemes.xml_io import dumps_scheme_tree
from tests.schemes.conftest import FIXTURE, load

SPEC_YAML = """
- id: door-schedule
  template: door-scheme.xml
  name: "Rebuilt Door Scheme"
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
    - caption: "Notes"
      bind: { gdl_param: "Notes Param" }
"""

# Describes the fixture's three columns exactly as they already exist, in
# their existing order (Door ID, Quantity, Fire Resistance): a no-op spec.
IDENTITY_SPEC_YAML = """
- id: identity
  columns:
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating Param" }
"""


def write_spec(tmp_path, text=SPEC_YAML):
    p = tmp_path / "schemes.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_a_spec(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path))
    assert errors == []
    assert len(specs) == 1
    assert specs[0].spec_id == "door-schedule"
    assert specs[0].template == "door-scheme.xml"
    assert [c.caption for c in specs[0].columns] == ["Quantity", "Door ID", "Notes"]


def test_apply_sets_the_column_list_and_order(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load()
    apply_spec(specs[0], scheme)
    assert [c.caption for c in scheme.columns] == ["Quantity", "Door ID", "Notes"]


def test_apply_sets_binding_kinds(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load()
    apply_spec(specs[0], scheme)
    kinds = {c.caption: c.binding.kind for c in scheme.columns}
    assert kinds["Quantity"] == KIND_BUILTIN
    assert kinds["Door ID"] == KIND_PROPERTY
    assert kinds["Notes"] == KIND_GDL_PARAM


def test_apply_renames_the_scheme(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load()
    apply_spec(specs[0], scheme)
    assert scheme.root.get("Name") == "Rebuilt Door Scheme"


def test_apply_returns_a_change_log(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    changes = apply_spec(specs[0], load())
    assert any("Notes" in c for c in changes)
    assert any("Fire Resistance" in c for c in changes)


def test_apply_of_an_identical_spec_is_a_true_no_op(tmp_path):
    """Regression test for the retarget-comparison defect: a spec whose
    columns exactly describe the fixture's current columns, in their current
    order, must report zero changes and must not alter a single byte of the
    file. Before the same_target fix, apply_spec compared bindings with full
    dataclass equality (column.binding != binding), so a spec-built Binding,
    which never carries the presentational property_name/desc_name a live
    XML binding does, compared unequal to the real one for both Door ID
    (property kind, property_name differs) and Fire Resistance (gdl_param
    kind, desc_name differs), retargeting both even though neither's actual
    target changed, and clobbering the presentational fields it had no
    instructions to touch."""
    specs, errors = load_specs(write_spec(tmp_path, IDENTITY_SPEC_YAML))
    assert errors == []
    scheme = load()
    original = FIXTURE.read_text(encoding="utf-8")

    changes = apply_spec(specs[0], scheme)

    retargeted = [c for c in changes if "retargeted" in c]
    assert retargeted == [], retargeted
    assert dumps_scheme_tree(scheme.tree) == original


def test_retarget_skipped_when_binding_differs_only_in_presentational_fields(tmp_path):
    """binding_from_bind never sets property_name for a property bind given
    as a GUID, the branch IDENTITY_SPEC_YAML's Door ID exercises here
    (bind: { property: "<guid>" }); the resolver branch, taken instead when
    a bind names a property by "Group/Name", does set property_name, to the
    name itself (see test_named_property_uses_the_resolver). Separately,
    binding_from_bind always sets desc_name to the gdl_param's own name
    rather than whatever Parameter_Desc_Name the file happens to carry.
    Neither difference is a real retarget: Door ID exercises the
    property-by-GUID kind (same GUID, property_name differs) and Fire
    Resistance exercises the gdl_param kind (same property_name, desc_name
    differs). Both presentational fields must survive untouched, proving
    retarget_column was never called."""
    specs, _ = load_specs(write_spec(tmp_path, IDENTITY_SPEC_YAML))
    scheme = load()

    changes = apply_spec(specs[0], scheme)

    assert not any("retargeted" in c for c in changes), changes
    by_caption = {c.caption: c.binding for c in scheme.columns}
    assert by_caption["Door ID"].property_name == "Door ID"
    assert by_caption["Fire Resistance"].desc_name == "Fire Resistance"


def test_retarget_still_fires_when_the_target_actually_changes(tmp_path):
    """Guards against a fix that simply disables retargeting: pointing Door
    ID at a genuinely different property GUID must still retarget, while
    the untouched columns must not."""
    spec_text = """
- id: retarget-check
  columns:
    - caption: "Door ID"
      bind: { property: "99999999-1111-4000-8000-000000000099" }
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating Param" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    scheme = load()

    changes = apply_spec(specs[0], scheme)

    assert any("retargeted column 'Door ID'" in c for c in changes), changes
    assert not any("retargeted column 'Quantity'" in c for c in changes), changes
    assert not any("retargeted column 'Fire Resistance'" in c for c in changes), changes
    by_caption = {c.caption: c.binding for c in scheme.columns}
    assert by_caption["Door ID"].property_guid == "99999999-1111-4000-8000-000000000099"


def test_same_target_property_kind_matches_on_guid_regardless_of_name():
    a = Binding(kind=KIND_PROPERTY, property_guid="AAAAAAAA-0000-0000-0000-000000000001",
               property_name="Door ID")
    b = Binding(kind=KIND_PROPERTY, property_guid="AAAAAAAA-0000-0000-0000-000000000001",
               property_name="")
    assert same_target(a, b) is True


def test_same_target_property_kind_differs_on_guid():
    a = Binding(kind=KIND_PROPERTY, property_guid="AAAAAAAA-0000-0000-0000-000000000001")
    b = Binding(kind=KIND_PROPERTY, property_guid="BBBBBBBB-0000-0000-0000-000000000002")
    assert same_target(a, b) is False


def test_same_target_gdl_param_kind_matches_on_property_name_regardless_of_desc():
    a = Binding(kind=KIND_GDL_PARAM, property_name="Fire Rating Param",
               desc_name="Fire Resistance")
    b = Binding(kind=KIND_GDL_PARAM, property_name="Fire Rating Param",
               desc_name="Fire Rating Param")
    assert same_target(a, b) is True


def test_same_target_gdl_param_kind_differs_on_property_name():
    a = Binding(kind=KIND_GDL_PARAM, property_name="Fire Rating Param")
    b = Binding(kind=KIND_GDL_PARAM, property_name="Some Other Param")
    assert same_target(a, b) is False


def test_same_target_builtin_kind_matches_on_type_and_index():
    a = Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1003)
    b = Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1003)
    assert same_target(a, b) is True


def test_same_target_builtin_kind_differs_on_index():
    a = Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1003)
    b = Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1004)
    assert same_target(a, b) is False


def test_same_target_cross_kind_is_always_false_even_with_overlapping_fields():
    """kind is checked first: even if a property binding's GUID string
    happens to equal a gdl_param binding's property_name string, the
    differing kind alone must decide it."""
    as_property = Binding(kind=KIND_PROPERTY, property_guid="SHARED-VALUE")
    as_gdl_param = Binding(kind=KIND_GDL_PARAM, property_name="SHARED-VALUE")
    assert same_target(as_property, as_gdl_param) is False


def test_named_property_without_a_resolver_is_an_error(tmp_path):
    spec_text = """
- id: s
  template: t.xml
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    with pytest.raises(SpecError):
        apply_spec(specs[0], load())


def test_named_property_uses_the_resolver(tmp_path):
    spec_text = """
- id: s
  template: t.xml
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    scheme = load()
    apply_spec(specs[0], scheme, resolver=lambda n: "AAAA1111-0000-0000-0000-000000000000")
    assert scheme.columns[0].binding.property_guid == "AAAA1111-0000-0000-0000-000000000000"


def test_malformed_yaml_is_reported_not_raised(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- id: x\n  bind: [unclosed\n", encoding="utf-8")
    specs, errors = load_specs(p)
    assert specs == []
    assert errors and "bad.yaml" in errors[0]


def test_spec_missing_id_is_reported(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path, "- template: t.xml\n  columns: []\n"))
    assert specs == []
    assert errors and "entry 0 is missing 'id'" in errors[0]


def test_template_is_optional(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n  columns: []\n"))
    assert errors == []
    assert specs[0].template is None


def test_criteria_block_is_reported_as_ignored_not_silently_dropped(tmp_path):
    spec_text = """
- id: s
  criteria:
    - element_class: Door
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    changes = apply_spec(specs[0], load())
    assert any("IGNORED the criteria block" in c for c in changes)


def test_spec_with_duplicate_column_captions_is_reported(tmp_path):
    """add_column and rename_column (columns.py) raise DuplicateColumnCaption
    because captions are the only key used to address a column. A YAML spec
    that lists the same caption twice would eventually hit that guard deep
    inside apply_spec, as a confusing exception pointing at the column layer
    instead of at the YAML file that actually caused it. load_specs must
    catch this at load time instead, the same way it catches a missing 'id'
    or a column missing 'caption'/'bind': collected into errors, the spec
    excluded from the returned list, nothing raised."""
    spec_text = """
- id: dup-caption
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Quantity"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert specs == []
    assert errors
    assert "dup-caption" in errors[0]
    assert "Quantity" in errors[0]


def test_apply_rejects_duplicate_captions_even_in_a_hand_built_spec():
    """load_specs is the primary guard, but SchemeSpec/ColumnSpec are plain
    dataclasses: nothing stops a caller (a future tool, a test, Task 7's live
    resolver path) from building one directly without going through
    load_specs. apply_spec must refuse a spec with duplicate captions on its
    own, with a clear SpecError, rather than letting the first duplicate
    reach add_column/rename_column and surface as DuplicateColumnCaption.
    Checked before any mutation runs, so a rejected spec leaves the scheme
    untouched rather than half-edited."""
    scheme = load()
    before = [c.caption for c in scheme.columns]
    spec = SchemeSpec(spec_id="s", columns=[
        ColumnSpec(caption="Quantity", bind={"builtin": "Quantity"}),
        ColumnSpec(caption="Quantity",
                  bind={"property": "69A58F6F-1111-4000-8000-000000000001"}),
    ])
    with pytest.raises(SpecError):
        apply_spec(spec, scheme)
    assert [c.caption for c in scheme.columns] == before


# --- Finding 1: a malformed 'columns' or 'criteria' shape must be collected
# as an error, never left to reach a "for c in <scalar>" loop and raise an
# uncaught TypeError. ---

def test_columns_int_scalar_is_reported_not_raised(tmp_path):
    """columns: 5 is truthy, so the old `entry.get("columns") or []` passed
    it straight into `for c in columns`, raising an uncaught TypeError
    instead of being collected like every other malformed shape."""
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n  columns: 5\n"))
    assert specs == []
    assert errors and "'columns' that is not a list, got int" in errors[0]


def test_columns_true_scalar_is_reported_not_raised(tmp_path):
    """Confirmed live: columns: true reaches the same loop and raises the
    same uncaught TypeError, since True is also truthy."""
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n  columns: true\n"))
    assert specs == []
    assert errors and "'columns' that is not a list, got bool" in errors[0]


def test_columns_false_scalar_is_reported_too(tmp_path):
    """False is falsy, so the old `or []` fallback silently treated it as no
    columns at all. columns must be a list whenever it is present, regardless
    of truthiness, so a nonsensical scalar is reported the same way either
    side of that line."""
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n  columns: false\n"))
    assert specs == []
    assert errors and "'columns' that is not a list, got bool" in errors[0]


def test_columns_absent_still_means_no_columns(tmp_path):
    """The fix must not turn 'columns' from optional into required: omitting
    it entirely is still valid and still means zero columns."""
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n"))
    assert errors == []
    assert specs[0].columns == []


def test_criteria_non_list_scalar_is_reported_not_raised(tmp_path):
    """The same class of bug as columns: criteria is never iterated inside
    load_specs itself, but a truthy non-list scalar was still stored
    verbatim on the SchemeSpec (errors == []), only to blow up later in
    apply_spec's `len(spec.criteria)`. load_specs must catch this at load
    time the same way it catches a malformed columns shape."""
    spec_text = "- id: s\n  criteria: 5\n  columns: []\n"
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert specs == []
    assert errors and "'criteria' that is not a list, got int" in errors[0]


# --- Finding 2: a column's bind shape (a mapping naming exactly one
# recognised kind) must be validated during load_specs, not left until
# apply_spec calls binding_from_bind. ---

def test_bind_empty_mapping_is_reported_by_load_specs(tmp_path):
    spec_text = """
- id: s
  columns:
    - caption: "X"
      bind: {}
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert specs == []
    assert errors and "column 'X' has an invalid bind" in errors[0]
    assert "Got: {}" in errors[0]


def test_bind_two_keys_is_reported_by_load_specs(tmp_path):
    spec_text = """
- id: s
  columns:
    - caption: "X"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001", builtin: Quantity }
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert specs == []
    assert errors and "bind must name exactly one of property, gdl_param, builtin" in errors[0]
    assert "'property'" in errors[0] and "'builtin'" in errors[0]


def test_bind_unrecognised_kind_is_reported_by_load_specs(tmp_path):
    spec_text = """
- id: s
  columns:
    - caption: "X"
      bind: { nonsense: "value" }
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert specs == []
    assert errors and "Unknown bind kind 'nonsense'" in errors[0]


def test_binding_from_bind_still_rejects_empty_mapping_directly():
    """binding_from_bind is the last line of defence for a SchemeSpec built
    directly rather than loaded from YAML (compare
    test_apply_rejects_duplicate_captions_even_in_a_hand_built_spec). The
    shared shape check must not remove this."""
    with pytest.raises(SpecError):
        binding_from_bind({})


def test_binding_from_bind_still_rejects_two_keys_directly():
    with pytest.raises(SpecError):
        binding_from_bind({"builtin": "Quantity", "property": "x"})


# --- Finding 3: apply_spec must resolve every column's binding before
# mutating anything, so a spec with one invalid column leaves the scheme
# completely untouched rather than partially edited. ---

def test_apply_leaves_scheme_completely_untouched_when_a_column_bind_is_invalid(tmp_path):
    """Confirmed live: a one-column spec with an invalid bind, applied
    against the three-column fixture, used to remove every existing column
    (none of them named 'Whatever') before binding_from_bind ever ran for
    'Whatever' itself, leaving scheme.columns == [] once the SpecError
    finally fired. Resolving every binding up front must raise before a
    single column is touched."""
    spec_text = """
- id: bad-bind
  columns:
    - caption: "Whatever"
      bind: { builtin: NoSuchField }
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert errors == []
    scheme = load()
    before_captions = [c.caption for c in scheme.columns]
    before_bytes = FIXTURE.read_text(encoding="utf-8")

    with pytest.raises(SpecError):
        apply_spec(specs[0], scheme)

    assert [c.caption for c in scheme.columns] == before_captions
    assert dumps_scheme_tree(scheme.tree) == before_bytes


# --- Finding 4: binding_from_bind's builtin branch also accepts a mapping of
# the raw Parameter_Type/Parameter_Index numbers, for built-in fields not yet
# in the small named BUILTIN_FIELDS table. ---

def test_builtin_mapping_form_produces_the_right_binding():
    binding = binding_from_bind({"builtin": {"param_type": 0, "param_index": -1561}})
    assert binding.kind == KIND_BUILTIN
    assert binding.param_type == 0
    assert binding.param_index == -1561


def test_builtin_mapping_missing_a_key_is_an_error():
    with pytest.raises(SpecError):
        binding_from_bind({"builtin": {"param_type": 0}})


def test_builtin_mapping_with_a_non_integer_value_is_an_error():
    with pytest.raises(SpecError):
        binding_from_bind({"builtin": {"param_type": 0, "param_index": "abc"}})


def test_builtin_named_form_still_works():
    binding = binding_from_bind({"builtin": "Quantity"})
    assert binding.kind == KIND_BUILTIN
    assert binding.param_type == 1
    assert binding.param_index == -1003


def test_apply_adds_a_column_using_the_builtin_mapping_escape_hatch(tmp_path):
    """End to end through load_specs and apply_spec: a spec can express a
    built-in column that BUILTIN_FIELDS has no name for yet, by giving the
    raw Parameter_Type/Parameter_Index pair instead of a name."""
    spec_text = """
- id: s
  columns:
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating Param" }
    - caption: "Mystery Field"
      bind: { builtin: { param_type: 0, param_index: -1561 } }
"""
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert errors == []
    scheme = load()
    apply_spec(specs[0], scheme)
    by_caption = {c.caption: c.binding for c in scheme.columns}
    assert by_caption["Mystery Field"].kind == KIND_BUILTIN
    assert by_caption["Mystery Field"].param_type == 0
    assert by_caption["Mystery Field"].param_index == -1561


def test_builtin_list_value_raises_spec_error_not_type_error():
    """A builtin bind whose value is a list (unhashable) must raise
    SpecError, not TypeError. This was the live bug: the membership test
    'value not in BUILTIN_FIELDS' raised TypeError: unhashable type: 'list'
    instead of producing a clear SpecError message."""
    with pytest.raises(SpecError):
        binding_from_bind({"builtin": [0, -1]})


def test_builtin_number_value_raises_spec_error():
    """A builtin bind whose value is a number must also raise SpecError,
    not be silently rejected. Numbers are hashable but not valid forms."""
    with pytest.raises(SpecError):
        binding_from_bind({"builtin": 123})


# --- Finding 1: a spec's `width` used to be written with set_field
# unconditionally and never appended to `changes`, so dry_run could show
# changes == [] for an edit that had, in fact, already rewritten the file.
# Worse, the fixture (like some real exports) has no Width_of_cell_landscape
# at all, and set_field's element-creation fallback appended one with no
# indentation or trailing newline of its own: well-formed XML, but visibly
# malformed next to every hand-formatted sibling. ---

_WIDTH_SPEC_YAML = """
- id: s
  columns:
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
      width: 55
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating Param" }
"""


def test_width_change_is_applied_and_reported_in_changes(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path, _WIDTH_SPEC_YAML))
    assert errors == []
    scheme = load()

    changes = apply_spec(specs[0], scheme)

    assert any("width" in c and "Door ID" in c for c in changes), changes
    door_id = next(c for c in scheme.columns if c.caption == "Door ID")
    assert field_value(door_id.element, "Width_of_cell_portrait") == "55"


def test_identity_spec_with_the_same_width_is_a_true_no_op(tmp_path):
    """Regression test for the silent-dry-run defect: a width spec that
    already matches the column's current Width_of_cell_portrait must report
    zero changes and must not alter a single byte of the file, exactly like
    every other identity spec (see
    test_apply_of_an_identical_spec_is_a_true_no_op). Before the fix,
    apply_spec called set_field unconditionally for width, so this same spec
    silently rewrote the file (the same bytes, but rewritten nonetheless)
    while still reporting changes == [], which is exactly backwards for a
    tool whose headline safety promise is that dry_run shows every change it
    is about to make."""
    spec_text = _WIDTH_SPEC_YAML.replace("width: 55", "width: 30")
    specs, errors = load_specs(write_spec(tmp_path, spec_text))
    assert errors == []
    scheme = load()
    original = FIXTURE.read_text(encoding="utf-8")

    changes = apply_spec(specs[0], scheme)

    assert changes == []
    assert dumps_scheme_tree(scheme.tree) == original


def test_width_change_reports_a_missing_landscape_field_instead_of_inventing_one(tmp_path):
    """The fixture's Header_Items have no Width_of_cell_landscape at all.
    Before the fix, set_field's ET.SubElement fallback created one with no
    indentation and no trailing newline of its own. The fix leaves a missing
    field untouched and says so in the change log instead of inventing it."""
    specs, _ = load_specs(write_spec(tmp_path, _WIDTH_SPEC_YAML))
    scheme = load()

    changes = apply_spec(specs[0], scheme)

    assert any("Width_of_cell_landscape" in c and "Door ID" in c for c in changes), changes
    door_id = next(c for c in scheme.columns if c.caption == "Door ID")
    assert door_id.element.find("Width_of_cell_landscape") is None


def test_width_is_updated_on_a_column_that_already_has_a_landscape_field(tmp_path):
    """Companion to the missing-field test above: when a column's XML does
    carry Width_of_cell_landscape, a real width change must update it too,
    not just Width_of_cell_portrait."""
    specs, _ = load_specs(write_spec(tmp_path, _WIDTH_SPEC_YAML))
    scheme = load()
    door_id = next(c for c in scheme.columns if c.caption == "Door ID")
    landscape = ET.SubElement(door_id.element, "Width_of_cell_landscape")
    landscape.set("value", "30")

    apply_spec(specs[0], scheme)

    assert field_value(door_id.element, "Width_of_cell_landscape") == "55"


def test_serialisation_is_well_formed_after_a_width_change(tmp_path):
    """Regression test for the malformed-XML half of finding 1: applying a
    real width change must never leave a freshly created field jammed
    against its parent's closing tag with no indentation (the specific shape
    set_field's element-creation fallback used to produce), and the output
    must still obey the same formatting invariants as an unmodified file (no
    " />", exactly one trailing newline), and must still be parseable XML."""
    specs, _ = load_specs(write_spec(tmp_path, _WIDTH_SPEC_YAML))
    scheme = load()

    apply_spec(specs[0], scheme)
    dumped = dumps_scheme_tree(scheme.tree)

    assert ET.fromstring(dumped) is not None
    assert " />" not in dumped
    assert dumped.endswith("\n") and not dumped.endswith("\n\n")
    assert "/></Header_Item>" not in dumped
    assert 'Width_of_cell_portrait value="55"' in dumped
