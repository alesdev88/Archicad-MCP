import textwrap

from archicad_mcp.rules.loader import RULE_TYPES, load_rules


def test_rule_types_registry_complete():
    assert set(RULE_TYPES) == {
        "property-required", "classification-required", "layer-compliance",
        "zone-number-required", "ifc-property-required",
    }


def test_load_rules_from_yaml_dir(tmp_path):
    (tmp_path / "office.yaml").write_text(textwrap.dedent("""\
        - id: walls-fire-rating
          type: property-required
          property: "Fire Rating"
          applies_to: { element_type: Wall }
          severity: error
          tags: [ifc-delivery]
        - id: zones-numbered
          type: zone-number-required
    """))
    loaded = load_rules(tmp_path)
    assert loaded.errors == []
    assert [r.rule_id for r in loaded.rules] == ["walls-fire-rating", "zones-numbered"]


def test_bad_rules_collect_errors_but_load_valid_ones(tmp_path):
    (tmp_path / "mixed.yaml").write_text(textwrap.dedent("""\
        - id: good-rule
          type: zone-number-required
        - id: bad-type
          type: no-such-type
        - type: property-required
          property: X
    """))
    (tmp_path / "broken.yaml").write_text("::: not yaml {{{")
    loaded = load_rules(tmp_path)
    assert [r.rule_id for r in loaded.rules] == ["good-rule"]
    assert len(loaded.errors) == 3  # unknown type, missing id, unparsable file


def test_custom_rules_plugin(tmp_path):
    (tmp_path / "custom_rules.py").write_text(textwrap.dedent("""\
        from archicad_mcp.rules.types import RuleResult

        class EverythingFineRule:
            rule_id = "custom-fine"
            severity = "warning"
            tags = frozenset()
            needs = frozenset({"elements"})
            needed_properties = frozenset()
            def check(self, snapshot):
                return RuleResult(self.rule_id, True, self.severity, "all fine")

        RULES = [EverythingFineRule()]
    """))
    loaded = load_rules(tmp_path)
    assert [r.rule_id for r in loaded.rules] == ["custom-fine"]


def test_none_dir_loads_bundled_examples():
    loaded = load_rules(None)
    assert loaded.errors == []
    assert loaded.rules, "bundled examples must provide at least one rule"
    assert loaded.source == "bundled examples"


def test_non_utf8_file_reports_error_and_does_not_raise(tmp_path):
    (tmp_path / "latin.yaml").write_bytes(b"- id: caf\xe9\n  type: zone-number-required\n")
    loaded = load_rules(tmp_path)
    assert len(loaded.errors) == 1
    assert loaded.rules == []


def test_non_string_rule_type_reports_error_and_does_not_raise(tmp_path):
    (tmp_path / "bad-type.yaml").write_text(textwrap.dedent("""\
        - id: weird
          type: [property-required]
    """))
    loaded = load_rules(tmp_path)
    assert len(loaded.errors) == 1
    assert loaded.rules == []
