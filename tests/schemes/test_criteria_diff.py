import shutil
from pathlib import Path

from scripts.diff_scheme_criteria import diff_criteria

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


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
