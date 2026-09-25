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
    assert result["skipped"][0]["errors"] == ["a classification system 'ELEA 2' already exists"]


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
