import json
from dataclasses import replace
from pathlib import Path

import pytest

from archicad_mcp.core.part_records import (
    COMBOS,
    HEADS,
    KINDS,
    PartRecord,
    PartRecords,
    RecordsFileError,
    identity_problem,
)
from archicad_mcp.data_dir import data_root
from archicad_mcp.tapir_install.install import backup_root


def bed(**overrides) -> PartRecord:
    fields = dict(
        name="Bed Double 01", guid="6B4F1A52-0C3E-4D1B-9A77-2E5C8D90F311",
        kind="object", learned="2026-10-22", tapir="1.7.2",
        footprint={"xMin": [-0.5, 0.0], "xMax": [0.5, 0.0],
                   "yMin": [0.0, 0.0], "yMax": [1.0, 0.0]},
        head="+y", one_sided=True, height=0.55,
    )
    fields.update(overrides)
    return PartRecord(**fields)


def window(**overrides) -> PartRecord:
    fields = dict(
        name="Window 26", guid="0D93E7B8-5A41-4C2F-8E16-71B3A4C5D622",
        kind="window", learned="2026-10-22", tapir="1.7.2",
        combos={"ff": ["begin", "left"], "tf": ["end", "left"],
                "ft": ["begin", "right"], "tt": ["end", "right"]},
        leaves=2, leaf_states={"left open": {"gs_leaf_1_open": True}},
    )
    fields.update(overrides)
    return PartRecord(**fields)


def test_data_root_per_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_root("win32") == tmp_path / "archicad-mcp"
    assert data_root("darwin") == Path.home() / "Library" / "Application Support" / "archicad-mcp"
    assert data_root("linux") == Path.home() / ".local" / "share" / "archicad-mcp"
    assert backup_root("win32") == tmp_path / "archicad-mcp" / "tapir-backups"


def test_windows_without_localappdata_falls_back_to_the_user_profile(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert data_root("win32") == Path.home() / "AppData" / "Local" / "archicad-mcp"


def test_the_vocabularies_the_tools_validate_against():
    assert KINDS == ("object", "lamp", "door", "window")
    assert COMBOS == ("ff", "tf", "ft", "tt")
    assert HEADS == ("+x", "-x", "+y", "-y")


def test_saved_record_reads_back_after_restart(tmp_path):
    PartRecords(tmp_path / "r.json").save(bed())
    assert PartRecords(tmp_path / "r.json").get("Bed Double 01") == bed()


def test_an_opening_with_combos_and_leaf_states_reads_back(tmp_path):
    PartRecords(tmp_path / "r.json").save(window())
    assert PartRecords(tmp_path / "r.json").get("Window 26") == window()


def test_an_opening_keeps_the_flipped_of_the_wall_it_was_learned_in(tmp_path):
    PartRecords(tmp_path / "r.json").save(window(wall_flipped=True))
    kept = PartRecords(tmp_path / "r.json").get("Window 26")
    assert kept.wall_flipped is True and kept.to_json()["wall_flipped"] is True


def test_a_record_written_before_wall_flipped_reads_it_as_unknown():
    older = {key: value for key, value in window().to_json().items() if key != "wall_flipped"}
    assert PartRecord.from_json(older).wall_flipped is None


def test_the_file_is_versioned_and_keyed_by_name(tmp_path):
    PartRecords(tmp_path / "r.json").save(bed())
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["parts"]["Bed Double 01"] == bed().to_json()


def test_the_default_file_sits_in_the_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    records = PartRecords()
    records.save(bed())
    assert (tmp_path / "library-parts.json").is_file()


def test_a_missing_file_is_an_empty_store(tmp_path):
    records = PartRecords(tmp_path / "none" / "r.json")
    assert records.get("Bed Double 01") is None
    assert records.all() == []
    assert not (tmp_path / "none").exists()


def test_saving_again_replaces_the_record_under_that_name(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    records.save(bed())
    records.save(bed(head="-x"))
    assert [r.head for r in records.all()] == ["-x"]


def test_names_with_other_alphabets_survive_the_file(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    records.save(bed(name="Postelja širok 02"))
    assert PartRecords(tmp_path / "r.json").get("Postelja širok 02") is not None


def test_a_record_does_not_share_its_dicts_with_the_store(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    record = bed()
    records.save(record)
    record.footprint["xMin"][0] = 99.0
    assert records.get("Bed Double 01").footprint["xMin"] == [-0.5, 0.0]


def test_missing_names_what_the_agent_has_not_recorded():
    assert bed(head=None, one_sided=None).missing() == ["head", "one_sided"]
    assert window(combos=None).missing() == ["combos"]


def test_missing_is_empty_for_a_complete_record():
    assert bed().missing() == []
    assert window().missing() == []


def test_a_lamp_needs_what_an_object_needs_and_a_door_what_a_window_needs():
    assert bed(kind="lamp", footprint=None).missing() == ["footprint"]
    assert window(kind="door", combos=None, leaves=None).missing() == ["combos", "leaves"]


def test_a_door_does_not_need_a_footprint_nor_a_bed_combos():
    assert window(footprint=None, head=None).missing() == []
    assert bed(combos=None, leaves=None).missing() == []


def test_a_record_of_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        bed(kind="wall")


def test_another_main_guid_refuses_the_record():
    problem = identity_problem(bed(guid="G1"), "G2")
    assert "learned for another part" in problem
    assert "G1" in problem and "G2" in problem
    assert bed().name in problem
    assert identity_problem(bed(guid="G1"), "g1") is None   # GUIDs compare without case


def test_forget_removes_only_that_part(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    records.save(bed())
    records.save(window())
    assert records.forget("Bed Double 01") is True
    assert PartRecords(tmp_path / "r.json").get("Bed Double 01") is None
    assert PartRecords(tmp_path / "r.json").get("Window 26") == window()


def test_forgetting_a_part_nobody_learned_says_so_and_changes_nothing(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    records.save(window())
    before = (tmp_path / "r.json").read_bytes()
    assert records.forget("Bed Double 01") is False
    assert (tmp_path / "r.json").read_bytes() == before


def test_all_lists_every_record(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    records.save(bed())
    records.save(window())
    assert sorted(r.name for r in records.all()) == ["Bed Double 01", "Window 26"]


def test_an_unreadable_file_is_never_overwritten(tmp_path):
    path = tmp_path / "r.json"; path.write_text("{not json")
    with pytest.raises(RecordsFileError):
        PartRecords(path).save(bed())
    assert path.read_text() == "{not json"


@pytest.mark.parametrize("text", [
    "{not json",
    "[]",
    '{"version": 1}',
    '{"version": 1, "parts": []}',
    '{"version": 2, "parts": {}}',
    '{"parts": {}}',
])
def test_every_read_of_an_unreadable_file_names_it(tmp_path, text):
    path = tmp_path / "r.json"; path.write_text(text)
    records = PartRecords(path)
    for call in (lambda: records.get("x"), records.all, lambda: records.forget("x"),
                 lambda: records.save(bed())):
        with pytest.raises(RecordsFileError, match="r.json"):
            call()
    assert path.read_text() == text


def test_a_file_that_is_not_utf8_is_unreadable(tmp_path):
    path = tmp_path / "r.json"; path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(RecordsFileError, match="r.json"):
        PartRecords(path).get("x")


def test_a_record_with_a_missing_field_names_the_part_and_the_file(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"version": 1, "parts": {"Bed": {"name": "Bed"}}}))
    with pytest.raises(RecordsFileError, match="Bed.*r.json|r.json.*Bed"):
        PartRecords(path).get("Bed")


def test_saving_next_to_a_damaged_record_keeps_it_as_written(tmp_path):
    # Only the named record is touched: another part's entry, even one this
    # version cannot read, goes back to the file exactly as it was.
    path = tmp_path / "r.json"
    odd = {"name": "Odd", "guid": "G", "kind": "object", "learned": "2026-01-01", "future": 1}
    path.write_text(json.dumps({"version": 1, "parts": {"Odd": odd}}))
    PartRecords(path).save(bed())
    assert json.loads(path.read_text())["parts"]["Odd"] == odd


def test_a_failed_write_leaves_the_old_file_and_no_stray_temporary(tmp_path, monkeypatch):
    path = tmp_path / "r.json"
    records = PartRecords(path)
    records.save(bed())
    before = path.read_bytes()

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("archicad_mcp.core.part_records.os.replace", broken_replace)
    with pytest.raises(RecordsFileError, match="r.json"):
        records.save(replace(bed(), head="-x"))
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_a_successful_save_leaves_only_the_file(tmp_path):
    PartRecords(tmp_path / "r.json").save(bed())
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]
