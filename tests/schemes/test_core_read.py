from pathlib import Path

from archicad_mcp.core.schemes import read_schedule_scheme

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def test_reports_scheme_header():
    out = read_schedule_scheme(str(FIXTURE))
    assert out["name"] == "Sample Door Scheme"
    assert out["scheme_id"] == "9001"
    assert out["column_count"] == 3


def test_describes_each_column_binding_in_words():
    out = read_schedule_scheme(str(FIXTURE))
    rows = {c["caption"]: c for c in out["columns"]}
    assert rows["Door ID"]["binds_to"] == "property"
    assert rows["Quantity"]["binds_to"] == "builtin"
    assert rows["Fire Resistance"]["binds_to"] == "gdl_param"
    assert rows["Fire Resistance"]["detail"] == "Fire Rating Param"
    assert rows["Door ID"]["index"] == 0


def test_reports_criteria():
    out = read_schedule_scheme(str(FIXTURE))
    assert len(out["criteria"]) == 2
    assert out["criteria"][0]["target"] == "D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"


def test_missing_file_returns_an_error_envelope():
    out = read_schedule_scheme("/nonexistent/nope.xml")
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_non_scheme_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text('<?xml version="1.0"?>\n<BuildingInformation/>\n', encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "Scheme_Settings" in out["error"]


def test_scheme_without_a_root_header_item_is_rejected(tmp_path):
    bad = tmp_path / "rootless.xml"
    bad.write_text('<?xml version="1.0"?>\n<Scheme_Settings ID="1" Name="X">'
                   "<Header_Items/></Scheme_Settings>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "root Header_Item" in out["error"]


def test_malformed_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "broken.xml"
    bad.write_text("<Scheme_Settings><unclosed>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "not valid XML" in out["error"]
