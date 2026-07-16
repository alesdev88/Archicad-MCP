import json
import textwrap

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

TIER1 = {"list_instances", "get_model_summary", "list_rules", "run_rule",
         "audit_delivery_readiness", "verify_ifc_export_readiness",
         "highlight_failures", "create_issues_from_failures"}


@pytest.fixture
def fake_archicad(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["HighlightElements"] = {}
    tapir["CreateIssue"] = {"issueId": {"guid": "issue-1"}}
    tapir["AttachElementsToIssue"] = {}
    core = FakeCore(official=api_replays.OFFICIAL, tapir=tapir)
    conn = ArchicadConnection(19723, core=core)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    monkeypatch.setattr(
        server_mod, "discover_instances",
        lambda: [InstanceInfo(19723, 29, 5003, "Test House", True, "1.8.2")])
    return core


def rules_dir(tmp_path):
    (tmp_path / "rules.yaml").write_text(textwrap.dedent("""\
        - id: zones-numbered
          type: zone-number-required
        - id: walls-fire-ifc
          type: ifc-property-required
          property: "Pset_WallCommon.FireRating"
          applies_to: { element_type: Wall }
          tags: [ifc-delivery]
    """))
    return tmp_path


async def call(mcp, tool, args=None):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_verdicts_mode_registers_only_tier1(tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == TIER1


async def test_list_instances(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_instances")
    assert payload["instances"][0]["port"] == 19723
    assert payload["instances"][0]["tapir_available"] is True


async def test_list_instances_masks_project_name_in_verdicts_mode(fake_archicad, tmp_path):
    """Verdicts mode promises no project info leaves; project_name must be None."""
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_instances")
    inst = payload["instances"][0]
    assert inst["project_name"] is None
    assert inst["tapir_version"] == "1.8.2"  # non-identifying fields still present


async def test_list_instances_keeps_project_name_in_full_mode(fake_archicad, tmp_path):
    mcp = build_server(mode="full", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_instances")
    assert payload["instances"][0]["project_name"] == "Test House"


async def test_list_rules_reports_loaded_rules(tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_rules")
    assert {r["id"] for r in payload["rules"]} == {"zones-numbered", "walls-fire-ifc"}
    assert payload["errors"] == []


async def test_audit_returns_scored_verdict(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness")
    # zone z-1 has number 101 -> zones-numbered passes; w-2 misses IFC FireRating -> fails
    by_rule = {r["rule"]: r for r in payload["results"]}
    assert by_rule["zones-numbered"]["pass"] is True
    assert by_rule["walls-fire-ifc"]["pass"] is False
    assert by_rule["walls-fire-ifc"]["guids"] == ["w-2"]
    assert payload["score"] == 50


async def test_audit_with_ruleset_tag_filters(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness", {"ruleset": "ifc-delivery"})
    assert [r["rule"] for r in payload["results"]] == ["walls-fire-ifc"]


async def test_audit_unknown_ruleset_tag_errors_not_perfect_score(fake_archicad, tmp_path):
    """A typo'd ruleset tag must error (listing known tags), never yield an empty
    score:100/pass:true false-positive verdict."""
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness", {"ruleset": "no-such-tag"})
    assert "error" in payload
    assert "no-such-tag" in payload["error"]
    assert "ifc-delivery" in payload["error"]  # the known tag is listed
    assert "score" not in payload and "pass" not in payload


async def test_run_rule_single(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "run_rule", {"rule_id": "zones-numbered"})
    assert payload["results"][0]["rule"] == "zones-numbered"


async def test_run_rule_unknown_id_is_actionable(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "run_rule", {"rule_id": "nope"})
    assert "nope" in payload["error"] and "zones-numbered" in payload["error"]


async def test_get_model_summary_default_is_type_only_and_safe(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "get_model_summary")
    assert payload["by_type"] == {"Wall": 2, "Zone": 1}
    # Default must NOT fetch per-element properties (the call that crashes AC).
    assert "by_layer" not in payload and "by_story" not in payload
    assert not any(c == "API.GetPropertyValuesOfElements"
                   for c, _ in fake_archicad.calls)
    assert "elements" not in payload  # aggregates only, no raw dumps


async def test_get_model_summary_layer_story_opt_in(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "get_model_summary", {"include_layer_story": True})
    assert payload["by_layer"] == {"A-WALL": 1, "Sketch": 1, "A-ZONE": 1}
    # story from floorIndex fixture: w-1=0, z-1=0, w-2=1
    assert payload["by_story"] == {"0": 2, "1": 1}


async def test_highlight_failures_calls_tapir(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "highlight_failures", {"rule_id": "walls-fire-ifc"})
    assert payload["highlighted"] == 1
    assert any(c == "HighlightElements" for c, _ in fake_archicad.calls)


async def test_create_issues_from_failures(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "create_issues_from_failures", {"rule_id": "walls-fire-ifc"})
    assert payload["issue_created"] is True and payload["attached"] == 1


async def test_archicad_down_gives_actionable_error(monkeypatch, tmp_path):
    from archicad_mcp.connection import ArchicadUnavailableError

    def boom(port):
        raise ArchicadUnavailableError("No running Archicad found. Start Archicad 29 and open a project.")

    monkeypatch.setattr(server_mod, "get_connection", boom)
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness")
    assert payload["error"].startswith("No running Archicad")


async def test_api_error_during_command_becomes_tool_error(monkeypatch, tmp_path):
    """Archicad answers the probe but a command errors (e.g. no project open):
    the StandardAPIError must be wrapped in the {"error": ...} envelope, not
    leaked as a protocol-level ToolError."""
    from multiconn_archicad.errors import StandardAPIError

    def no_project(_parameters):
        raise StandardAPIError(message="No open project", code=-402)

    official = dict(api_replays.OFFICIAL)
    official["API.GetAllElements"] = no_project
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    conn = ArchicadConnection(19723, core=core)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "get_model_summary")
    assert "error" in payload
    assert "No open project" in payload["error"]
    assert "project" in payload["error"].lower()
