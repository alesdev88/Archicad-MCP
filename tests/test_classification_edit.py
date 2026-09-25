from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.classification_edit import edit_classifications
from tests.fixtures.definition_edit_replays import fake_editing_core


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def test_item_code_and_name_change_plan():
    conn, core = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "code": "40.11",
                                          "name": "Kuhinjska oprema"}])
    assert result["planned"][0] == {"target": "ELEA/40.10", "changes": {
        "code": ["40.10", "40.11"], "name": ["Kuhinja", "Kuhinjska oprema"]}}
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


def test_code_colliding_with_a_sibling_is_an_error():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "code": "40.20"}])
    assert result["skipped"][0]["errors"] == ["code '40.20' is already used by a sibling (ELEA/40.20)"]


def test_branch_address_is_refused_for_item_edits():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40/*", "name": "X"}])
    assert "one item" in result["skipped"][0]["errors"][0]


def test_system_rename_and_bad_date():
    conn, _ = _conn()
    result = edit_classifications(conn, [
        {"system": "ELEA", "name": "ELEA 2026", "version": "2"},
        {"system": "ELEA 2", "date": "25.9.2026"}])
    assert result["planned"][0]["changes"] == {"name": ["ELEA", "ELEA 2026"], "version": ["1", "2"]}
    assert result["skipped"][0]["errors"] == ["date must be YYYY-MM-DD"]


def test_system_rename_onto_existing_name_is_an_error():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"system": "ELEA", "name": "ELEA 2"}])
    assert result["skipped"][0]["errors"] == [
        "a classification system 'ELEA 2' already exists in version '1'"]


def test_renaming_into_a_free_version_is_allowed():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"system": "ELEA", "name": "ELEA 2", "version": "2"}])
    assert "skipped" not in result


def test_system_edit_on_a_shared_name_is_ambiguous():
    from tests.conftest import FakeCore
    systems = {"classificationSystems": [
        {"classificationSystemId": {"guid": "u1"}, "name": "Uniclass", "version": "1.30"},
        {"classificationSystemId": {"guid": "u2"}, "name": "Uniclass", "version": "1.31"}]}
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True},
                              "API.GetAllClassificationSystems": systems,
                              "API.GetAllClassificationsInSystem": {"classificationItems": []}})
    result = edit_classifications(ArchicadConnection(19724, core=core),
                                  [{"system": "Uniclass", "description": "x"}])
    assert "names 2 systems" in result["skipped"][0]["errors"][0]


def test_commit_sends_systems_then_items():
    conn, core = _conn()
    edit_classifications(conn, [{"item": "ELEA/40.10", "name": "K"},
                                {"system": "ELEA", "description": "d"}], dry_run=False)
    order = [cmd for cmd, _ in core.calls if cmd.startswith("Update")]
    assert order == ["UpdateClassificationSystems", "UpdateClassificationItems"]
    items = [p for cmd, p in core.calls if cmd == "UpdateClassificationItems"][0]
    assert items == {"classificationItems": [
        {"classificationItemId": {"guid": "i-40-10"}, "name": "K"}]}


def test_classification_edit_refuses_old_tapir():
    conn, core = _conn(marker=False)
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "name": "K"}], dry_run=False)
    assert "UpdateClassificationItems" in result["error"]
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


# ---------- import_definitions ----------

from pathlib import Path

from archicad_mcp.core.classification_edit import import_definitions

XML = Path(__file__).parent / "fixtures" / "xml"

_SIFRA_XML = """<BuildingInformation><PropertyDefinitionGroups><PropertyDefinitionGroup>
<Name>ELEA</Name><PropertyDefinitions>
<PropertyDefinition><Name>Sifra</Name></PropertyDefinition>
<PropertyDefinition><Name>Nova</Name></PropertyDefinition>
</PropertyDefinitions></PropertyDefinitionGroup></PropertyDefinitionGroups></BuildingInformation>"""


def test_import_dry_run_reports_new_and_colliding(tmp_path):
    path = tmp_path / "p.xml"
    path.write_text(_SIFRA_XML, encoding="utf-8")
    conn, core = _conn()
    result = import_definitions(conn, "property", str(path), "skip")
    assert result["dry_run"] is True
    assert result["new"] == ["ELEA/Nova"]
    assert result["collisions"] == ["ELEA/Sifra"]
    assert "stay as they are" in result["policy"]
    assert not any(cmd.startswith("Import") for cmd, _ in core.calls)


def test_import_classification_dry_run_uses_system_and_code():
    conn, _ = _conn()
    result = import_definitions(conn, "classification",
                                str(XML / "classifications_mcp_test.xml"), "merge")
    assert "MCP Test/Wall" in result["new"] and result["collisions"] == []


def test_import_rejects_an_unknown_policy():
    conn, _ = _conn()
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"), "merge")
    assert result["error"] == "conflict for property imports is one of ['append', 'replace', 'skip']"


def test_import_commit_sends_the_file_and_reports_created():
    created = {"executionResult": {"success": True},
               "created": [{"guid": "p-code"}], "removed": []}
    conn, core = _conn(extra_tapir={"ImportPropertiesXml": created})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "append", dry_run=False)
    sent = [p for cmd, p in core.calls if cmd == "ImportPropertiesXml"][0]
    assert sent["conflictPolicy"] == "append" and sent["xml"].lstrip().startswith("<")
    assert result["created"] == ["ELEA/Sifra"]


def test_import_refused_by_archicad_reports_the_message():
    refused = {"executionResult": {"success": False, "error": {"code": 1, "message": "invalid property XML"}},
               "created": [], "removed": []}
    conn, _ = _conn(extra_tapir={"ImportPropertiesXml": refused})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "skip", dry_run=False)
    assert result["error"] == "invalid property XML"


def test_import_refuses_a_missing_file():
    conn, _ = _conn()
    result = import_definitions(conn, "property", "/nope/missing.xml", "skip")
    assert "cannot read" in result["error"]


def test_import_refuses_malformed_xml(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text("<unclosed>", encoding="utf-8")
    conn, _ = _conn()
    result = import_definitions(conn, "property", str(path), "skip")
    assert result["error"].startswith("not valid XML")
