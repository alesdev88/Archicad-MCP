"""The definition editors in a Teamwork project name the set to reserve, and
refuse to send while it is free or held by a teammate.

Property and classification definitions live in the undocumented
ClassificationsAndProperties set: Archicad 29's and 30's APICore list it with
the documented names, and CVP answered a status for it on 19.10.2026 (read
only). Whether Archicad refuses an unreserved edit, and how, is still unseen;
the tools refuse first. A status that cannot be learned only warns.
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
    assert result["planned"] == []
    assert RESERVE in result["skipped"][0]["errors"][0]


def test_an_unreserved_set_blocks_the_commit():
    conn, core = _conn(teamwork={SET: ("Free", [])})
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}],
                                       dry_run=False)
    assert not any(c == "UpdatePropertyDefinitions" for c, _ in core.calls)
    assert RESERVE in result["skipped"][0]["errors"][0]
    assert "applied" not in result


def test_an_unavailable_server_only_warns_and_hints_on_failure():
    conn, core = _conn(teamwork={SET: ("NotAvailable", [])}, update=refused)
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}],
                                       dry_run=False)
    assert any(c == "UpdatePropertyDefinitions" for c, _ in core.calls)
    [group] = result["failed"]
    assert "unavailable" in group["hint"]


def test_an_unreadable_status_only_warns():
    from multiconn_archicad.errors import TapirCommandError

    def broken(params):
        raise TapirCommandError(message="Invalid program status", code=4001)
    conn, core = _conn(teamwork={SET: ("Free", [])},
                       extra_tapir={"GetLockableObjectSetStatus": broken})
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}],
                                       dry_run=False)
    assert any(c == "UpdatePropertyDefinitions" for c, _ in core.calls)
    assert "could not check" in result["teamwork"][0]["problem"]


def test_a_reserved_set_carries_no_problem_and_sends():
    conn, core = _conn(teamwork={SET: ("Editable", [])})
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}],
                                       dry_run=False)
    assert "problem" not in result["teamwork"][0]
    assert result["applied"][0]["target"] == "ELEA/Sifra"


def test_classification_edits_held_by_a_teammate_are_refused():
    conn, core = _conn(teamwork={SET: ("Locked", ["Someone Else"])})
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "name": "K"}],
                                  dry_run=False)
    assert "Someone Else" in result["skipped"][0]["errors"][0]
    assert not any(c.startswith("UpdateClassification") for c, _ in core.calls)


def test_import_dry_run_says_it_is_blocked():
    conn, core = _conn(teamwork={SET: ("Free", [])})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip")
    assert RESERVE in result["teamwork"][0]["problem"]
    assert RESERVE in result["blocked"]


def test_import_commit_is_refused_before_sending():
    conn, core = _conn(teamwork={SET: ("Free", [])})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip", dry_run=False)
    assert RESERVE in result["error"]
    assert not any(c == "ImportPropertiesXml" for c, _ in core.calls)


def test_import_refusal_carries_the_hint_when_the_status_is_unknown():
    refused_import = {"executionResult": {"success": False, "error": {
        "code": 1, "message": "no access right"}}, "created": [], "removed": []}
    conn, _ = _conn(teamwork={SET: ("NotAvailable", [])},
                    extra_tapir={"ImportPropertiesXml": refused_import})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip", dry_run=False)
    assert result["error"] == "no access right" and "unavailable" in result["hint"]
