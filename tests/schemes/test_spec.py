from pathlib import Path

import pytest

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    parse_scheme,
)
from archicad_mcp.schemes.spec import (
    ColumnSpec,
    SchemeSpec,
    SpecError,
    apply_spec,
    load_specs,
)
from archicad_mcp.schemes.xml_io import load_scheme_tree

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
