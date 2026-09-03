"""The criteria evaluator is pure, so every operator is tested here without a
transport. Cell shapes mirror the live AC 29 property cells (status normal /
userUndefined / notAvailable) recorded in tests/fixtures/api_replays.py."""
import pytest

from archicad_mcp.criteria import (
    Cell,
    ClassificationTree,
    Comparison,
    CriteriaError,
    CriteriaGroup,
    comparison_matches,
    evaluate_classification,
    evaluate_value,
    group_matches,
    needs_story,
    parse_comparison,
    parse_groups,
    properties_referenced,
    systems_referenced,
)

NORMAL = Cell("normal", "EI60")
EMPTY = Cell("normal", "")
UNDEFINED = Cell("userUndefined")
UNAVAILABLE = Cell("notAvailable")


# ---------- cells ----------

def test_cell_from_api_maps_the_three_statuses_and_the_error_envelope():
    assert Cell.from_api({"type": "string", "status": "normal", "value": "x"}) == Cell("normal", "x")
    assert Cell.from_api({"type": "string", "status": "userUndefined"}).status == "userUndefined"
    assert Cell.from_api({"type": "string", "status": "notAvailable"}).status == "notAvailable"
    assert Cell.from_api({}).status == "notAvailable"   # error cell, envelope stripped
    assert Cell.from_api(None).status == "notAvailable"


def test_cell_unwraps_enum_ids_to_strings():
    single = Cell.from_api({"type": "singleEnum", "status": "normal",
                            "value": {"displayValue": "On"}})
    assert single.value == "On"
    multi = Cell.from_api({"type": "multiEnum", "status": "normal",
                           "value": [{"nonLocalizedValue": "A"}, {"displayValue": "B"}]})
    assert multi.value == ["A", "B"]


def test_cell_from_value_treats_none_as_user_undefined():
    assert Cell.from_value(None).status == "userUndefined"
    assert Cell.from_value("x") == Cell("normal", "x")


# ---------- the four senses of empty ----------

@pytest.mark.parametrize("op,cell,expected", [
    ("has_value", NORMAL, True), ("has_value", EMPTY, False),
    ("has_value", UNDEFINED, False), ("has_value", UNAVAILABLE, False),
    ("has_no_value", NORMAL, False), ("has_no_value", EMPTY, True),
    ("has_no_value", UNDEFINED, True), ("has_no_value", UNAVAILABLE, True),
    ("is_user_undefined", UNDEFINED, True), ("is_user_undefined", EMPTY, False),
    ("is_user_undefined", UNAVAILABLE, False),
    ("is_not_user_undefined", UNDEFINED, False), ("is_not_user_undefined", NORMAL, True),
    ("not_available", UNAVAILABLE, True), ("not_available", UNDEFINED, False),
    ("available", UNAVAILABLE, False), ("available", UNDEFINED, True),
])
def test_unary_operators(op, cell, expected):
    assert evaluate_value(op, cell) is expected


def test_empty_list_has_no_value():
    assert evaluate_value("has_value", Cell("normal", [])) is False


# ---------- binary operators ----------

def test_binary_operators_never_match_a_missing_value():
    for op, wanted in [("equal", "EI60"), ("not_equal", "x"), ("contains", "E"),
                       ("does_not_contain", "zzz"), ("less", 5), ("greater", 0)]:
        assert evaluate_value(op, UNDEFINED, wanted) is False
        assert evaluate_value(op, UNAVAILABLE, wanted) is False


def test_string_equality_is_case_insensitive():
    assert evaluate_value("equal", NORMAL, "ei60") is True
    assert evaluate_value("not_equal", NORMAL, "ei60") is False


@pytest.mark.parametrize("op,wanted,expected", [
    ("contains", "i6", True), ("contains", "x", False),
    ("does_not_contain", "x", True), ("does_not_contain", "EI", False),
    ("starts_with", "ei", True), ("starts_with", "60", False),
    ("ends_with", "60", True), ("ends_with", "EI", False),
])
def test_string_operators(op, wanted, expected):
    assert evaluate_value(op, NORMAL, wanted) is expected


def test_numeric_equality_has_a_tolerance_for_metre_doubles():
    assert evaluate_value("equal", Cell("normal", 2.9999999999), 3) is True
    assert evaluate_value("equal", Cell("normal", 2.99), 3) is False


@pytest.mark.parametrize("op,wanted,expected", [
    ("less", 3.5, True), ("less", 3, False), ("less_or_equal", 3, True),
    ("greater", 2, True), ("greater", 3, False), ("greater_or_equal", 3, True),
])
def test_ordering_on_numbers(op, wanted, expected):
    assert evaluate_value(op, Cell("normal", 3.0), wanted) is expected


def test_ordering_on_strings_is_lexicographic_and_case_folded():
    assert evaluate_value("less", Cell("normal", "apple"), "Banana") is True
    assert evaluate_value("greater", Cell("normal", "apple"), "Banana") is False


def test_number_against_string_is_not_comparable():
    assert evaluate_value("less", Cell("normal", 3.0), "four") is False
    assert evaluate_value("equal", Cell("normal", 3.0), "3") is False


def test_booleans():
    assert evaluate_value("equal", Cell("normal", True), True) is True
    assert evaluate_value("equal", Cell("normal", True), False) is False
    assert evaluate_value("not_equal", Cell("normal", False), True) is True


def test_lists_match_when_any_member_does():
    multi = Cell("normal", ["A", "B"])
    assert evaluate_value("equal", multi, "b") is True
    assert evaluate_value("contains", multi, "a") is True
    assert evaluate_value("does_not_contain", multi, "a") is False
    assert evaluate_value("equal", multi, ["a", "b"]) is True
    assert evaluate_value("equal", multi, ["a"]) is False


# ---------- classifications ----------

TREE = ClassificationTree([
    {"classificationItem": {"classificationItemId": {"guid": "g-site"}, "id": "Site", "children": [
        {"classificationItem": {"classificationItemId": {"guid": "g-geo"}, "id": "Geographic Element",
                                "children": [
            {"classificationItem": {"classificationItemId": {"guid": "g-terrain"}, "id": "Terrain"}},
        ]}},
    ]}},
    {"classificationItem": {"classificationItemId": {"guid": "g-wall"}, "id": "Wall"}},
])


def test_tree_resolves_ids_case_insensitively_and_guids():
    assert TREE.resolve("terrain") == "g-terrain"
    assert TREE.resolve("g-geo") == "g-geo"
    assert TREE.resolve("nope") is None
    assert TREE.ancestors("g-terrain") == ["g-geo", "g-site"]
    assert TREE.parent("g-site") is None


@pytest.mark.parametrize("op,wanted,expected", [
    ("equal", "Terrain", True), ("equal", "g-terrain", True), ("equal", "Site", False),
    ("not_equal", "Site", True),
    ("is_in_branch_of", "Site", True), ("is_in_branch_of", "Terrain", True),
    ("is_in_branch_of", "Wall", False),
    ("is_not_in_branch_of", "Wall", True), ("is_not_in_branch_of", "Site", False),
    ("is_direct_child_of", "Geographic Element", True), ("is_direct_child_of", "Site", False),
    ("is_not_direct_child_of", "Site", True),
    ("has_value", None, True),
])
def test_classification_operators(op, wanted, expected):
    assert evaluate_classification(op, Cell("normal", "g-terrain"), wanted, TREE) is expected


def test_unclassified_element_has_no_value_and_matches_no_branch():
    missing = Cell("userUndefined")
    assert evaluate_classification("has_no_value", missing, None, TREE) is True
    assert evaluate_classification("is_in_branch_of", missing, "Site", TREE) is False
    assert evaluate_classification("is_not_in_branch_of", missing, "Site", TREE) is False


def test_unknown_target_item_matches_only_the_negations():
    cell = Cell("normal", "g-terrain")
    assert evaluate_classification("is_in_branch_of", cell, "Nope", TREE) is False
    assert evaluate_classification("is_not_in_branch_of", cell, "Nope", TREE) is True


def test_without_a_tree_only_identity_tests_work():
    cell = Cell("normal", "g-terrain")
    assert evaluate_classification("equal", cell, "g-terrain", None) is True
    assert evaluate_classification("is_in_branch_of", cell, "g-site", None) is False


# ---------- parsing ----------

def test_parse_comparison_kinds():
    assert parse_comparison({"property": "OFFICE/Fire Rating", "operator": "has_value"}).kind == "property"
    c = parse_comparison({"property": "classification:Archicad Classification",
                          "operator": "is_in_branch_of", "value": "Wall"})
    assert c.kind == "classification" and c.system == "Archicad Classification"
    assert parse_comparison({"property": "story", "operator": "equal", "value": 0}).kind == "story"


@pytest.mark.parametrize("raw,fragment", [
    ({"property": "x", "operator": "has_value", "value": 1}, "takes no 'value'"),
    ({"property": "x", "operator": "equal"}, "needs a 'value'"),
    ({"property": "x", "operator": "like", "value": 1}, "unknown operator"),
    ({"property": "", "operator": "has_value"}, "'property'"),
    ({"property": "x", "operator": "is_in_branch_of", "value": "Wall"}, "classification:"),
    ({"property": "classification:", "operator": "has_value"}, "system name"),
    ({"property": "story", "operator": "contains", "value": "1"}, "'story'"),
    ({"property": "story", "operator": "equal", "value": "ground"}, "integer"),
    ({"property": "x", "operator": "equal", "value": 1, "extra": 2}, "unknown keys"),
    ("not a dict", "expected an object"),
])
def test_parse_comparison_rejects_malformed_input(raw, fragment):
    with pytest.raises(CriteriaError, match=fragment):
        parse_comparison(raw)


def test_parse_groups_defaults_and_validation():
    groups = parse_groups([{"element_types": "Wall"}])
    assert groups[0].element_types == ("Wall",)
    assert groups[0].element_types_operator == "is" and groups[0].logical_operator == "and"
    with pytest.raises(CriteriaError, match="non-empty list"):
        parse_groups([])
    with pytest.raises(CriteriaError, match="match every element"):
        parse_groups([{}])
    assert parse_groups([{"element_types": ["all"]}])[0].element_types == ()
    assert parse_groups([{"element_types": ["ALL", "Wall"]}])[0].element_types == ()
    with pytest.raises(CriteriaError, match="'is' or 'is_not'"):
        parse_groups([{"element_types": ["Wall"], "element_types_operator": "not"}])
    with pytest.raises(CriteriaError, match="'and' or 'or'"):
        parse_groups([{"element_types": ["Wall"], "logical_operator": "xor"}])
    with pytest.raises(CriteriaError, match="unknown keys"):
        parse_groups([{"element_types": ["Wall"], "layer": "x"}])


# ---------- groups ----------

def _cells(mapping):
    return lambda c: mapping.get(c.property, UNAVAILABLE)


def test_group_and_or_and_type_filters():
    group = CriteriaGroup(comparisons=(
        Comparison("A", "equal", "1"), Comparison("B", "equal", "2")),
        element_types=("Wall",))
    both = _cells({"A": Cell("normal", "1"), "B": Cell("normal", "2")})
    one = _cells({"A": Cell("normal", "1"), "B": Cell("normal", "x")})
    assert group_matches(group, "Wall", both) is True
    assert group_matches(group, "Wall", one) is False
    assert group_matches(group, "Slab", both) is False
    or_group = CriteriaGroup(group.comparisons, ("Wall",), "is", "or")
    assert group_matches(or_group, "Wall", one) is True
    not_walls = CriteriaGroup((), ("Wall",), "is_not")
    assert group_matches(not_walls, "Slab", both) is True
    assert group_matches(not_walls, "Wall", both) is False


def test_group_uses_the_tree_for_classification_comparisons():
    group = CriteriaGroup(comparisons=(Comparison(
        "classification:AC", "is_in_branch_of", "Site"),))
    cells = _cells({"classification:AC": Cell("normal", "g-terrain")})
    assert group_matches(group, "Mesh", cells, lambda system: TREE) is True
    assert group_matches(group, "Mesh", cells, lambda system: None) is False


def test_referenced_helpers():
    groups = parse_groups([
        {"comparisons": [{"property": "A/B", "operator": "has_value"},
                         {"property": "classification:AC", "operator": "has_value"},
                         {"property": "story", "operator": "equal", "value": 1}]},
        {"comparisons": [{"property": "A/B", "operator": "has_no_value"},
                         {"property": "ModelView_LayerName", "operator": "equal", "value": "x"}]},
    ])
    assert properties_referenced(groups) == ["A/B", "ModelView_LayerName"]
    assert systems_referenced(groups) == ["AC"]
    assert needs_story(groups) is True
    assert comparison_matches(groups[0].comparisons[2], Cell("normal", 1)) is True
