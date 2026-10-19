"""no-collisions: clashes as a QA rule, answered through the snapshot."""
from __future__ import annotations

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.collisions import Collision, CollisionQuery
from archicad_mcp.extract import build_snapshot
from archicad_mcp.rules.builtin.collisions import NoCollisionsRule
from archicad_mcp.rules.engine import collision_queries, run_rules
from archicad_mcp.rules.loader import RULE_TYPES
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError
from tests.conftest import FakeCore
from tests.fixtures import api_replays

BEAM, COLUMN, DUCT = "B" * 8 + "-0000-0000-0000-000000000001", \
    "C" * 8 + "-0000-0000-0000-000000000002", "D" * 8 + "-0000-0000-0000-000000000003"

CONFIG = {"id": "beams-vs-ducts", "type": "no-collisions",
          "element_types": ["Beam", "columns"], "against_types": ["Object"],
          "volume_tolerance": 0.002, "surface_tolerance": 0.01,
          "severity": "error", "tags": ["coordination"]}


def test_registered_as_a_rule_type():
    assert RULE_TYPES["no-collisions"] is NoCollisionsRule


def test_from_config():
    rule = NoCollisionsRule.from_config(CONFIG)
    assert rule.collision_query == CollisionQuery(
        element_types=("Beam", "Column"), against_types=("Object",),
        volume_tolerance=0.002, surface_tolerance=0.01)
    assert rule.needs == frozenset({"collisions"})
    assert rule.tags == frozenset({"coordination"})


def test_from_config_within_one_group():
    rule = NoCollisionsRule.from_config({"id": "x", "type": "no-collisions",
                                         "element_types": ["Wall"]})
    assert rule.collision_query == CollisionQuery(element_types=("Wall",))


def test_from_config_needs_element_types():
    with pytest.raises(RuleConfigError, match="element_types"):
        NoCollisionsRule.from_config({"id": "x", "type": "no-collisions"})


def test_from_config_unknown_type():
    with pytest.raises(RuleConfigError, match="Beem"):
        NoCollisionsRule.from_config({"id": "x", "type": "no-collisions",
                                      "element_types": ["Beem"]})


def test_check_fails_with_pairs():
    rule = NoCollisionsRule.from_config(CONFIG)
    snapshot = ModelSnapshot(collisions={rule.collision_query: (
        Collision(BEAM, DUCT, True, False), Collision(COLUMN, DUCT, True, False))})
    result = rule.check(snapshot)
    assert result.passed is False
    assert result.failure_count == 2
    assert sorted(result.failing_guids) == sorted([BEAM, COLUMN, DUCT])
    assert "2 collision(s)" in result.message


def test_check_passes_without_pairs():
    rule = NoCollisionsRule.from_config(CONFIG)
    result = rule.check(ModelSnapshot(collisions={rule.collision_query: ()}))
    assert result.passed is True and result.failure_count == 0


def test_skips_without_collision_data():
    rule = NoCollisionsRule.from_config(CONFIG)
    result = rule.check(ModelSnapshot(collisions=None))
    assert result.skipped is True
    assert "GetCollisions" in result.skip_reason
    assert run_rules([rule], ModelSnapshot()).score == 100


def _conn(with_command=True):
    official = dict(api_replays.OFFICIAL)
    if not with_command:
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] != "GetCollisions"}
    by_type = {"Beam": [BEAM], "Column": [COLUMN], "Object": [DUCT]}
    core = FakeCore(official=official, tapir={
        "GetElementsByType": lambda p: {"elements": [
            {"elementId": {"guid": g}} for g in by_type.get(p["elementType"], [])]},
        "GetCollisions": {"collisions": [
            {"elementId1": {"guid": BEAM}, "elementId2": {"guid": DUCT},
             "hasBodyCollision": True, "hasClearenceCollision": False}]},
    })
    return ArchicadConnection(19723, core=core), core


def test_build_snapshot_answers_queries():
    rule = NoCollisionsRule.from_config(CONFIG)
    conn, core = _conn()
    queries = collision_queries([rule])
    snapshot = build_snapshot(conn, rule.needs, collision_queries=queries)
    assert snapshot.collisions == {rule.collision_query: (Collision(BEAM, DUCT, True, False),)}
    sent = [p for name, p in core.calls if name == "GetCollisions"][0]
    assert [e["elementId"]["guid"] for e in sent["elementsGroup1"]] == [BEAM, COLUMN]
    assert sent["settings"]["performSurfaceCheck"] is True


def test_build_snapshot_without_queries_never_detects():
    conn, core = _conn()
    snapshot = build_snapshot(conn, frozenset({"layers"}))
    assert snapshot.collisions is None
    assert not [name for name, _ in core.calls if name == "GetCollisions"]


def test_build_snapshot_without_getcollisions():
    rule = NoCollisionsRule.from_config(CONFIG)
    conn, core = _conn(with_command=False)
    snapshot = build_snapshot(conn, rule.needs, collision_queries=collision_queries([rule]))
    assert snapshot.collisions is None
    assert not [name for name, _ in core.calls if name == "GetCollisions"]


async def test_run_rule_through_the_server(monkeypatch, tmp_path):
    import json
    import textwrap

    from fastmcp import Client

    import archicad_mcp.server as server_mod
    (tmp_path / "rules.yaml").write_text(textwrap.dedent("""\
        - id: beams-vs-objects
          type: no-collisions
          element_types: [Beam]
          against_types: [Object]
    """))
    conn, _ = _conn()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    mcp = server_mod.build_server(mode="verdicts", rules_dir=tmp_path)
    async with Client(mcp) as client:
        result = await client.call_tool("run_rule", {"rule_id": "beams-vs-objects"})
    payload = json.loads(result.content[0].text)["results"][0]
    assert payload["pass"] is False
    assert payload["failures"] == 1
    assert payload["guids"] == [BEAM, DUCT]
