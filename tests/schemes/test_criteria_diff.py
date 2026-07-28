import shutil

from scripts.diff_scheme_criteria import diff_criteria
from tests.schemes.conftest import FIXTURE


def test_identical_files_diff_to_nothing(tmp_path):
    a = tmp_path / "a.xml"
    shutil.copy(FIXTURE, a)
    assert diff_criteria(a, a) == []


def test_reports_a_changed_relation_index(tmp_path):
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    b.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        '<Relation_Index value="12"/>', '<Relation_Index value="7"/>', 1),
        encoding="utf-8")
    changes = diff_criteria(a, b)
    assert {"index": 1, "field": "Relation_Index", "before": "12", "after": "7"} in changes


def test_reports_a_criterion_count_change(tmp_path):
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    text = FIXTURE.read_text(encoding="utf-8")
    start = text.index("\t\t\t<Criterion>")
    end = text.index("</Criterion>", start) + len("</Criterion>\n")
    b.write_text(text[:start] + text[end:], encoding="utf-8")
    changes = diff_criteria(a, b)
    assert any(c["field"] == "criterion_count" for c in changes)


def test_reports_a_change_in_a_field_outside_the_watched_list(tmp_path):
    """UniValue/HasVariant was never in the old WATCHED allowlist, so the old
    _criterion_values could not see it change no matter what. The tool is
    for discovering fields nobody has named yet, so this must be visible."""
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    b.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        "<HasVariant>true</HasVariant>", "<HasVariant>false</HasVariant>", 1),
        encoding="utf-8")
    changes = diff_criteria(a, b)
    matches = [c for c in changes if c["field"] == "UniValue/HasVariant"]
    assert len(matches) == 1
    assert matches[0]["index"] == 0
    assert matches[0]["before"] == "true"
    assert matches[0]["after"] == "false"


def test_reports_a_new_field_and_marks_it_unrecognised(tmp_path):
    """A child element that does not exist at all in the before file is the
    most interesting possible result for this tool, so it must be reported
    and flagged so the researcher's eye is drawn to it."""
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    text = FIXTURE.read_text(encoding="utf-8")
    insert_at = text.index("</Criterion>")
    new_text = text[:insert_at] + '<Newly_Added_Field value="42"/>' + text[insert_at:]
    b.write_text(new_text, encoding="utf-8")
    changes = diff_criteria(a, b)
    matches = [c for c in changes if c["field"] == "Newly_Added_Field"]
    assert len(matches) == 1
    assert matches[0]["index"] == 0
    assert matches[0]["before"] == ""
    assert matches[0]["after"] == "42"
    assert matches[0].get("unrecognised") is True
