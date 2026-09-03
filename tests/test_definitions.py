"""search_definitions: fuzzy lookup over definitions, never values."""
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.definitions import fold, score, search_definitions
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_conn(tapir=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    return ArchicadConnection(19723, core=FakeCore(
        official=official, tapir=api_replays.TAPIR if tapir else {}))


# ---------- matching ----------

def test_fold_strips_diacritics_and_case():
    assert fold("Splošno") == "splosno"
    assert fold("VRATA") == "vrata"


def test_score_requires_every_token_and_prefers_exact_tokens():
    assert score("fire rating", ["OFFICE/Fire Rating"]) == 1.0
    assert score("fire", ["OFFICE/Fire Rating"]) == 1.0
    assert score("fir", ["OFFICE/Fire Rating"]) == pytest.approx(0.9)
    assert score("fire zzz", ["OFFICE/Fire Rating"]) == 0.0
    assert score("", ["x"]) == 0.0


def test_score_tolerates_a_typo_and_a_missing_space():
    assert score("ratng", ["OFFICE/Fire Rating"]) > 0
    assert score("firerating", ["OFFICE/Fire Rating"]) > 0


# ---------- the tool ----------

def test_property_matches_carry_the_address_the_other_tools_accept():
    payload = search_definitions(make_conn(), "fire rating")
    top = payload["matches"][0]
    assert top["kind"] == "property"
    assert top["property"] == "OFFICE/Fire Rating"     # user property: Group/Name
    assert top["editable"] is True and top["builtin"] is False
    assert top["value_type"] == "String"


def test_builtin_address_is_the_api_name_when_it_has_one_else_the_guid():
    payload = search_definitions(make_conn(), "layer name", kind="property")
    layer = next(m for m in payload["matches"] if m["name"] == "Model View/Layer Name")
    assert layer["property"] == "ModelView_LayerName"
    payload = search_definitions(make_conn(), "wall height", kind="property")
    height = payload["matches"][0]
    assert height["property"] == "11111111-1111-1111-1111-111111111111"
    assert height["measure_type"] == "Length"


def test_enum_values_are_searchable_and_returned():
    payload = search_definitions(make_conn(), "approved")
    assert payload["matches"][0]["property"] == "OFFICE/Status"
    assert payload["matches"][0]["enum_values"] == ["Approved", "Draft"]


def test_editable_only_drops_read_only_properties():
    payload = search_definitions(make_conn(), "height", kind="property", editable_only=True)
    assert payload["total_matches"] == 0


def test_attributes_search_by_type_and_name():
    payload = search_definitions(make_conn(), "wall", kind="attribute")
    names = {(m["attribute_type"], m["name"]) for m in payload["matches"]}
    assert ("Layer", "A-WALL") in names
    payload = search_definitions(make_conn(), "layer", kind="attribute")
    assert {m["attribute_type"] for m in payload["matches"]} == {"Layer"}


def test_kind_any_mixes_both_and_alternatives_widen_the_search():
    payload = search_definitions(make_conn(), "nothing-here", alternatives=["fire", "dashed"])
    kinds = {m["kind"] for m in payload["matches"]}
    assert kinds == {"property", "attribute"}


def test_limit_and_truncation():
    payload = search_definitions(make_conn(), "a", limit=1)
    assert len(payload["matches"]) == 1 and payload["truncated"] is True


def test_validation_errors():
    assert "error" in search_definitions(make_conn(), "x", kind="layers")
    assert "error" in search_definitions(make_conn(), "  ")
    assert "error" in search_definitions(make_conn(), "x", alternatives=list("abcdefg"))


def test_never_reads_property_values():
    conn = make_conn()
    search_definitions(conn, "fire")
    assert not any(c == "API.GetPropertyValuesOfElements" for c, _ in conn._core.calls)


def test_without_tapir_falls_back_to_the_official_api_and_says_so():
    conn = make_conn(tapir=False)
    payload = search_definitions(conn, "fire", kind="property")
    assert payload["matches"][0]["property"] == "OFFICE/Fire Rating"
    assert any("Tapir" in n for n in payload["notes"])
    assert not any(c == "GetAllProperties" for c, _ in conn._core.calls)


async def test_tool_is_registered_in_full_mode_only(monkeypatch):
    core = make_conn()._core
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    async with Client(build_server(mode="full")) as client:
        names = {t.name for t in await client.list_tools()}
        assert "search_definitions" in names
        result = await client.call_tool("search_definitions", {"query": "fire"})
        assert json.loads(result.content[0].text)["matches"][0]["property"] == "OFFICE/Fire Rating"
    async with Client(build_server(mode="verdicts")) as client:
        assert "search_definitions" not in {t.name for t in await client.list_tools()}
