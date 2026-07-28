from pathlib import Path

import pytest

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    Binding,
    parse_scheme,
    same_target,
)
from archicad_mcp.schemes.spec import (
    ColumnSpec,
    SchemeSpec,
    SpecError,
    apply_spec,
    load_specs,
)
from archicad_mcp.schemes.xml_io import dumps_scheme_tree, load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

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


def load_scheme():
    return parse_scheme(load_scheme_tree(FIXTURE))


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
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    assert [c.caption for c in scheme.columns] == ["Quantity", "Door ID", "Notes"]


def test_apply_sets_binding_kinds(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    kinds = {c.caption: c.binding.kind for c in scheme.columns}
    assert kinds["Quantity"] == KIND_BUILTIN
    assert kinds["Door ID"] == KIND_PROPERTY
    assert kinds["Notes"] == KIND_GDL_PARAM


def test_apply_renames_the_scheme(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    assert scheme.root.get("Name") == "Rebuilt Door Scheme"


def test_apply_returns_a_change_log(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    changes = apply_spec(specs[0], load_scheme())
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
    scheme = load_scheme()
    original = FIXTURE.read_text(encoding="utf-8")

    changes = apply_spec(specs[0], scheme)

    retargeted = [c for c in changes if "retargeted" in c]
    assert retargeted == [], retargeted
    assert dumps_scheme_tree(scheme.tree) == original


def test_retarget_skipped_when_binding_differs_only_in_presentational_fields(tmp_path):
    """binding_from_bind never sets property_name for a property bind, and
    always sets desc_name to the gdl_param's own name rather than whatever
    Parameter_Desc_Name the file happens to carry. Neither difference is a
    real retarget: Door ID exercises the property kind (same GUID,
    property_name differs) and Fire Resistance exercises the gdl_param kind
    (same property_name, desc_name differs). Both presentational fields
    must survive untouched, proving retarget_column was never called."""
    specs, _ = load_specs(write_spec(tmp_path, IDENTITY_SPEC_YAML))
    scheme = load_scheme()

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
    scheme = load_scheme()

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
        apply_spec(specs[0], load_scheme())


def test_named_property_uses_the_resolver(tmp_path):
    spec_text = """
- id: s
  template: t.xml
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    scheme = load_scheme()
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
    assert errors and "id" in errors[0]


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
    changes = apply_spec(specs[0], load_scheme())
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
    scheme = load_scheme()
    before = [c.caption for c in scheme.columns]
    spec = SchemeSpec(spec_id="s", columns=[
        ColumnSpec(caption="Quantity", bind={"builtin": "Quantity"}),
        ColumnSpec(caption="Quantity",
                  bind={"property": "69A58F6F-1111-4000-8000-000000000001"}),
    ])
    with pytest.raises(SpecError):
        apply_spec(spec, scheme)
    assert [c.caption for c in scheme.columns] == before
