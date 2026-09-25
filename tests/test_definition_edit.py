"""Definition editing: address resolution, plans, sending. No property VALUE is
ever read here; the fake core has no GetPropertyValuesOfElements on purpose."""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, editing_unavailable, group_by_error)
from tests.fixtures.definition_edit_replays import fake_editing_core


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def test_resolve_item_by_system_and_code():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA/40.10") == (["i-40-10"], None)


def test_branch_suffix_takes_the_item_and_everything_below():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/40.20/*")
    assert err is None
    assert guids == ["i-40-20", "i-40-20-1"]


def test_longest_system_name_wins():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA 2/40") == (["j-40"], None)
    assert index.resolve("ELEA/40") == (["i-40"], None)


def test_unknown_code_is_an_error_naming_the_address():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/99")
    assert guids == [] and "ELEA/99" in err


def test_a_guid_resolves_to_itself():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("i-40") == (["i-40"], None)


def test_label_and_siblings():
    index = ClassificationIndex.load(_conn()[0])
    assert index.label("i-40-20-1") == "ELEA/40.20.1"
    assert index.siblings("i-40-10") == ["i-40-20"]


def test_old_tapir_is_refused_before_anything_is_sent():
    conn, core = _conn(marker=False)
    result = editing_unavailable(conn)
    assert result is not None and "UpdateClassificationItems" in result["error"]
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


def test_failures_group_by_message():
    failed = [{"target": "ELEA/Sifra", "message": "no access right"},
              {"target": "ELEA/Kategorija", "message": "no access right"},
              {"target": "ELEA/Dolzina", "message": "name already used"}]
    groups = group_by_error(failed)
    assert groups[0] == {"message": "no access right", "count": 2,
                         "sample": ["ELEA/Sifra", "ELEA/Kategorija"]}
