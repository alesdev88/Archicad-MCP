"""The definition editors in a Teamwork project name the set to reserve.

Property and classification definitions are guessed to live in the
undocumented ClassificationsAndProperties set (Archicad 29's APICore string
table), so a missing reservation is a warning and a hint on failures, never a
block. Not verified live: the only Teamwork project here is production.
"""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.classification_edit import edit_classifications, import_definitions
from archicad_mcp.core.definition_edit import edit_property_definitions
from tests.fixtures.definition_edit_replays import fake_editing_core
from tests.test_classification_edit import XML

SET = "ClassificationsAndProperties"
RESERVE = 'reserve_elements(object_sets=["ClassificationsAndProperties"], confirm=true)'


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def refused(params):
    key = next(iter(params))
    return {"executionResults": [{"success": False, "error": {"code": -1, "message":
                                  "no access right"}} for _ in params[key]]}


def test_solo_project_results_carry_no_teamwork_key():
    conn, core = _conn()
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}])
    assert "teamwork" not in result
    assert not any(c == "GetLockableObjectSetStatus" for c, _ in core.calls)


def test_dry_run_in_teamwork_names_the_set_and_the_call():
    conn, _ = _conn(teamwork={SET: ("Free", [])})
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}])
    [entry] = result["teamwork"]
    assert entry["set"] == SET and entry["status"] == "Free"
    assert entry["needed_for"] == ["ELEA/Sifra"]
    assert RESERVE in entry["problem"]


def test_an_unreserved_set_still_sends_and_hints_on_failure():
    conn, core = _conn(teamwork={SET: ("Free", [])}, update=refused)
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}],
                                       dry_run=False)
    assert any(c == "UpdatePropertyDefinitions" for c, _ in core.calls)
    [group] = result["failed"]
    assert RESERVE in group["hint"]


def test_a_reserved_set_carries_no_problem():
    conn, _ = _conn(teamwork={SET: ("Editable", [])})
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}])
    assert "problem" not in result["teamwork"][0]


def test_classification_edits_name_the_holder():
    conn, _ = _conn(teamwork={SET: ("Locked", ["Someone Else"])})
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "name": "K"}])
    assert "Someone Else" in result["teamwork"][0]["problem"]


def test_import_dry_run_names_the_set():
    conn, _ = _conn(teamwork={SET: ("Free", [])})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip")
    assert RESERVE in result["teamwork"][0]["problem"]


def test_import_refusal_carries_the_hint():
    refused_import = {"executionResult": {"success": False, "error": {
        "code": 1, "message": "no access right"}}, "created": [], "removed": []}
    conn, _ = _conn(teamwork={SET: ("Free", [])},
                    extra_tapir={"ImportPropertiesXml": refused_import})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip", dry_run=False)
    assert result["error"] == "no access right" and RESERVE in result["hint"]
