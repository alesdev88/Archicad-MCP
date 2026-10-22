import json

from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create_placement import OBJECT_PLACEMENT
from archicad_mcp.core.part_records import PartRecord, PartRecords
from archicad_mcp.server import build_server
from tests.fixtures.learned_parts import bed_record
from tests.fixtures.modify_project import ModifyProject
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
                     "record_library_part": (False, False),
                     "list_library_part_records": (True, False)}
    for name in LIBRARY_PART_TOOLS:
        assert not {"\N{EM DASH}", "\N{EN DASH}"} & set(tools[name].description), name


async def _descriptions():
    async with Client(build_server(mode="full")) as client:
        return {t.name: t.description for t in await client.list_tools()}


async def test_no_tool_description_has_an_em_or_en_dash():
    for name, text in (await _descriptions()).items():
        assert not {"\N{EM DASH}", "\N{EN DASH}"} & set(text), name


async def test_the_descriptions_carry_the_architects_rules_for_reading_a_sketch():
    text = await _descriptions()
    create = text["create_elements"]
    # Learn once, then place with no samples.
    assert "learn_library_part" in create and "no samples" in create
    # The part comes from what the sketch draws: sashes from the dividing lines,
    # the opening one from the arcs, the plain generic part when it says no more.
    assert "sashes" in create and "arcs" in create and "Double Window" in create
    # Width is a check, and a disagreement is a question.
    assert "1.2 m" in create and "ask" in create
    # A bed's head is the pillow end, or the turned-back edge of the duvet.
    assert "pillows" in create and "duvet" in create
    # After placing: look, correct, then report.
    assert "capture_window" in create and "modify_elements" in create
    assert "capture_window" in text["modify_elements"]
    # The same reading rules where the agent records what the image shows.
    record = text["record_library_part"]
    assert "pillows" in record and "duvet" in record and "sashes" in record
    assert "ask" in record
    # A window learned without opening lines in the capture: ask to show them.
    learn = text["learn_library_part"]
    assert "sashes" in learn and "opening lines" in learn and "ask the user" in learn
    assert "no samples" in learn
    # What the records file takes and what it never touches.
    assert "replaces" in record and "forget" in record and "never the model" in record


async def test_the_descriptions_say_what_learning_cannot_do_on_tapir_1_7_2():
    text = await _descriptions()
    learn = text["learn_library_part"]
    # A window without opening lines: show them, then learn it again.
    assert "then learn the window again" in learn
    # Leaves on 1.7.2, said plainly.
    assert "cannot be read or written on doors and windows placed through the API" in learn
    assert "placed_not_set" in learn
    assert "rotated" in learn and "placed by coordinates" in learn


async def test_one_head_convention_in_every_text_that_names_it():
    text = await _descriptions()
    rule = "the end that goes against the wall: a bed's pillow end, a chair's or sofa's back"
    assert rule in text["record_library_part"]
    assert rule in text["create_elements"]
    assert rule.replace("the end", "The head is the end") in " ".join(
        OBJECT_PLACEMENT["properties"]["head"]["description"].split())
    assert "or the front points" not in OBJECT_PLACEMENT["properties"]["head"]["description"]


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


async def test_modify_elements_places_from_the_learned_parts_store(monkeypatch, tmp_path):
    monkeypatch.setattr("archicad_mcp.core.part_records.data_root", lambda: tmp_path)
    bed, part = "5349C8FC-A4BF-3548-A614-77BEB2CE4261", "B5D0A1C2-7E4F-4C11-9A3B-0D6E2F8A1B01"
    PartRecords().save(bed_record(guid=part))
    project = ModifyProject()
    project.set_part(bed, "Bed Double 01", part)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: project.connect())
    placement = {"center": {"x": 2.0, "y": 3.0}, "head": {"x": 0.0, "y": -1.0},
                 "size": {"length": 2.0, "width": 1.6, "height": 0.5}}
    async with Client(build_server(mode="full")) as client:
        out = await _call(client, "modify_elements",
                          {"changes": [{"guid": bed, "placement": placement}]})
    [planned] = out["planned"]
    assert planned["placement"] == {"part": "Bed Double 01"}
    assert set(planned["changes"]) == {"coordinates", "angle", "reflected", "dimensions"}
