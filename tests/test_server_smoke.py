import json

from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.part_records import PartRecord, PartRecords
from archicad_mcp.server import build_server
from tests.test_part_learning import Fake


async def test_server_builds_and_lists_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "list_rules" in names


async def test_full_mode_registers_the_schedule_scheme_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" in names
    assert "edit_schedule_scheme" in names
    assert "validate_schedule_scheme" in names


async def test_verdicts_mode_omits_the_schedule_scheme_tools():
    mcp = build_server(mode="verdicts")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" not in names
    assert "edit_schedule_scheme" not in names
    assert "validate_schedule_scheme" not in names


LIBRARY_PART_TOOLS = {"learn_library_part", "record_library_part", "list_library_part_records"}


async def test_the_three_tools_exist_in_full_mode_only():
    async with Client(build_server(mode="full")) as client:
        full = {t.name for t in await client.list_tools()}
    async with Client(build_server(mode="verdicts")) as client:
        verdicts = {t.name for t in await client.list_tools()}
    assert LIBRARY_PART_TOOLS <= full
    assert not LIBRARY_PART_TOOLS & verdicts


async def test_the_library_part_tools_declare_what_they_change():
    async with Client(build_server(mode="full")) as client:
        tools = {t.name: t for t in await client.list_tools()}
    hints = {name: (tools[name].annotations.readOnlyHint, tools[name].annotations.destructiveHint)
             for name in LIBRARY_PART_TOOLS}
    assert hints == {"learn_library_part": (False, True),
                     "record_library_part": (False, True),
                     "list_library_part_records": (True, False)}
    for name in LIBRARY_PART_TOOLS:
        assert not {"\N{EM DASH}", "\N{EN DASH}"} & set(tools[name].description), name


async def _call(client, tool, args):
    result = await client.call_tool(tool, args)
    return json.loads(result.content[-1].text)


async def test_recording_and_listing_share_one_store(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    PartRecords().save(PartRecord(name="Bed Double 01", guid="6B4F1A52", kind="object",
                                  learned="2026-10-22", footprint={"xMin": [0.0, 0.0]}))
    async with Client(build_server(mode="full")) as client:
        recorded = await _call(client, "record_library_part",
                               {"name": "Bed Double 01", "head": "+y"})
        listed = await _call(client, "list_library_part_records", {})
        refused = await _call(client, "record_library_part", {"name": "Chair 01", "head": "+y"})
    assert recorded["record"]["head"] == "+y" and recorded["missing"] == ["one_sided"]
    assert [r["head"] for r in listed["records"]] == ["+y"]
    assert listed["path"] == str(tmp_path / "library-parts.json")
    assert "learn_library_part" in refused["error"]


async def test_an_unreadable_file_does_not_stop_the_server_only_the_tools(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    (tmp_path / "library-parts.json").write_text("{broken", encoding="utf-8")
    async with Client(build_server(mode="full")) as client:
        listed = await _call(client, "list_library_part_records", {})
        recorded = await _call(client, "record_library_part", {"name": "A", "head": "+y"})
    assert str(tmp_path / "library-parts.json") in listed["error"]
    assert str(tmp_path / "library-parts.json") in recorded["error"]
    assert (tmp_path / "library-parts.json").read_text(encoding="utf-8") == "{broken"


async def test_learning_hands_back_the_picture_and_stores_the_measurement(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    fake = Fake()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: fake.connection())
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("learn_library_part",
                                        {"name": "Bed Double 01", "kind": "object"})
        recorded = await _call(client, "record_library_part",
                               {"name": "Bed Double 01", "head": "+y", "one_sided": False})
    assert [block.type for block in result.content] == ["image", "text"]
    assert result.content[0].mimeType == "image/png"
    reply = json.loads(result.content[1].text)
    assert "png" not in reply and reply["part"] == "Bed Double 01"
    assert reply["next"] == ["head", "one_sided"] and fake.remaining == []
    assert recorded["missing"] == [] and recorded["record"]["footprint"] == reply["measured"]


async def test_learning_names_the_tapir_it_needs_before_placing_anything(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    fake = Fake()
    fake.connection()
    core = fake.core
    core.official_responses["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] != "SaveCurrentWindowAsImage"}
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("learn_library_part",
                                        {"name": "Bed Double 01", "kind": "object"})
    assert [block.type for block in result.content] == ["text"]
    assert "1.7.2" in json.loads(result.content[0].text)["error"]
    assert fake.created_guids == []
