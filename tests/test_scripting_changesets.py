"""Changesets: stored plans that apply once, exactly as previewed."""
import pytest

from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.apply import apply_changeset
from archicad_mcp.scripting.changesets import (
    ChangesetError,
    ChangesetStore,
    group_skipped,
    summarize,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _write(guid, value, prop="OFFICE/Fire Rating"):
    return {"guid": guid, "property": prop, "current": None, "new": value,
            "payload": {"elementId": {"guid": guid},
                        "propertyId": {"guid": f"pid-{prop}"},
                        "propertyValue": {"type": "string", "status": "normal",
                                          "value": value}}}


def _props(*writes):
    return {"kind": "props", "writes": list(writes)}


# ---------- store ----------

def test_lookup_returns_what_was_added():
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    assert store.lookup(cs.id) is cs
    assert cs.id.startswith("cs-")


def test_changesets_expire():
    clock = Clock()
    store = ChangesetStore(ttl_s=60, clock=clock)
    cs = store.add(19723, "P", [], [])
    clock.now += 61
    with pytest.raises(ChangesetError, match="Unknown or expired"):
        store.lookup(cs.id)


def test_the_oldest_changeset_is_evicted_past_capacity():
    store = ChangesetStore(capacity=2)
    first = store.add(19723, "P", [], [])
    store.add(19723, "P", [], [])
    store.add(19723, "P", [], [])
    with pytest.raises(ChangesetError):
        store.lookup(first.id)


def test_an_applied_changeset_says_so():
    store = ChangesetStore()
    cs = store.add(19723, "P", [], [])
    cs.applied = True
    with pytest.raises(ChangesetError, match="already applied"):
        store.lookup(cs.id)


def test_summary_counts_and_samples():
    writes = [_write(f"g-{i}", "x") for i in range(25)]
    ops = [_props(*writes), {"kind": "command", "name": "HighlightElements",
                             "params": {}}]
    skipped = [{"guid": "s", "property": "p", "reason": "r"}]
    cs = ChangesetStore().add(19724, "Oprema-objekti", ops, skipped)
    summary = summarize(cs)
    assert summary["property_writes"] == 25
    assert summary["commands"] == {"HighlightElements": 1}
    assert summary["skipped"] == 1
    assert summary["skipped_reasons"] == [
        {"reason": "r", "count": 1, "sample": [{"guid": "s", "property": "p"}]}]
    assert len(summary["sample"]) == 20
    assert summary["sample"][0] == {"guid": "g-0", "property": "OFFICE/Fire Rating",
                                    "current": None, "new": "x"}
    assert summary["port"] == 19724 and summary["project"] == "Oprema-objekti"
    assert summary["expires_at"].endswith("Z")


# ---------- apply ----------

class Model:
    """A stateful fake: writes land in `values` and reads come from it."""

    def __init__(self):
        self.values = {}
        self.refuse = set()   # guids Archicad refuses (Teamwork 6001)
        self.ignore = set()   # guids Archicad reports as written but drops

    def set(self, params):
        results = []
        for item in params["elementPropertyValues"]:
            guid = item["elementId"]["guid"]
            if guid in self.refuse:
                results.append({"success": False, "error": {
                    "code": 6001, "message": "TeamWork permission denied"}})
                continue
            if guid not in self.ignore:
                key = (guid, item["propertyId"]["guid"])
                self.values[key] = item["propertyValue"]["value"]
            results.append({"success": True})
        return {"executionResults": results}

    def get(self, params):
        rows = []
        for el in params["elements"]:
            row = []
            for p in params["properties"]:
                key = (el["elementId"]["guid"], p["propertyId"]["guid"])
                cell = {"type": "string", "status": "normal"}
                if key in self.values:
                    cell["value"] = self.values[key]
                row.append({"propertyValue": cell})
            rows.append({"propertyValues": row})
        return {"propertyValuesForElements": rows}


@pytest.fixture
def world():
    model = Model()
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = model.set
    official["API.GetPropertyValuesOfElements"] = model.get
    tapir = dict(api_replays.TAPIR)
    core = FakeCore(official=official, tapir=tapir)
    project = {"name": "Test House"}

    def probe(port):
        return InstanceInfo(port=port, version=29, build=5101,
                            project_name=project["name"], tapir_available=True,
                            tapir_version="1.5.9")

    def run(store, cs_id, confirm=True):
        return apply_changeset(store, cs_id, confirm, build_registry(), probe=probe,
                               connect=lambda port: ArchicadConnection(port, core=core))

    return model, core, project, run


def _sets(core):
    return [c for c, _ in core.calls if c == "API.SetPropertyValuesOfElements"]


def test_apply_refuses_without_confirm_and_repeats_the_summary(world):
    model, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    out = run(store, cs.id, confirm=False)
    assert "confirm=true" in out["error"]
    assert out["changeset"]["property_writes"] == 1
    assert _sets(core) == []
    assert store.lookup(cs.id) is cs  # still available


def test_apply_refuses_when_another_project_is_open(world):
    _, core, project, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    project["name"] = "Other Project"
    out = run(store, cs.id)
    assert "Other Project" in out["error"] and "Nothing was written" in out["error"]
    assert _sets(core) == []


def test_apply_writes_reads_back_and_is_single_use(world):
    model, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House",
                   [_props(_write("w-1", "EI30"), _write("w-2", "EI90"))], [])
    out = run(store, cs.id)
    assert out == {"applied": 2, "failed": [], "mismatched": [], "commands": []}
    assert model.values[("w-1", "pid-OFFICE/Fire Rating")] == "EI30"
    with pytest.raises(ChangesetError, match="already applied"):
        store.lookup(cs.id)


def test_apply_reports_refusals_and_readback_mismatches(world):
    model, _, _, run = world
    model.refuse.add("w-1")
    model.ignore.add("w-2")
    store = ChangesetStore()
    cs = store.add(19723, "Test House",
                   [_props(_write("w-1", "EI30"), _write("w-2", "EI90"))], [])
    out = run(store, cs.id)
    assert out["applied"] == 1
    assert out["failed"] == [{"code": 6001, "message": "TeamWork permission denied",
                              "count": 1,
                              "sample": [{"guid": "w-1",
                                          "property": "OFFICE/Fire Rating"}]}]
    # The refused element is not read back: its failure is already reported.
    assert out["mismatched"] == [{"guid": "w-2", "property": "OFFICE/Fire Rating",
                                  "sent": "EI90", "read": None}]


def test_apply_stops_at_the_first_failed_command(world):
    model, core, _, run = world
    store = ChangesetStore()
    # HighlightElements has no canned response, so FakeCore refuses it.
    ops = [_props(_write("w-1", "EI30")),
           {"kind": "command", "name": "HighlightElements", "params": {}},
           _props(_write("w-2", "EI90"))]
    cs = store.add(19723, "Test House", ops, [])
    out = run(store, cs.id)
    assert out["applied"] == 1
    assert out["commands"] == [{"name": "HighlightElements", "ok": False,
                                "code": None,
                                "message": "FakeCore: no canned response for "
                                           "HighlightElements"}]
    assert "stopped" in out
    assert ("w-2", "pid-OFFICE/Fire Rating") not in model.values


def test_apply_groups_long_failure_lists(world):
    model, _, _, run = world
    guids = [f"g-{i}" for i in range(60)]
    model.refuse.update(guids)
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(*[_write(g, "x") for g in guids])], [])
    out = run(store, cs.id)
    [group] = out["failed"]
    assert group["count"] == 60 and len(group["sample"]) == 5
    assert "failed_not_shown" not in out


def test_apply_readback_over_ceiling(world, monkeypatch):
    model, _, _, run = world
    # Monkeypatch both module constants to trigger slicing.
    import archicad_mcp.scripting.apply
    import archicad_mcp.extract
    monkeypatch.setattr(archicad_mcp.scripting.apply, "MAX_PROPERTY_FETCH_ELEMENTS", 2)
    monkeypatch.setattr(archicad_mcp.extract, "MAX_PROPERTY_FETCH_ELEMENTS", 2)

    store = ChangesetStore()
    writes = [_write(f"g-{i}", f"x{i}") for i in range(5)]
    cs = store.add(19723, "Test House", [_props(*writes)], [])
    out = run(store, cs.id)
    # All 5 writes should be applied and read back without hitting ceiling.
    assert out["applied"] == 5
    assert out["failed"] == []
    assert out["mismatched"] == []
    assert out["commands"] == []


def test_apply_command_dispatch_unavailable(world, monkeypatch):
    from archicad_mcp.connection import ArchicadUnavailableError
    model, core, _, run = world
    # Monkeypatch _dispatch to raise ArchicadUnavailableError for the command.
    import archicad_mcp.scripting.apply
    original_dispatch = archicad_mcp.scripting.apply._dispatch
    def mock_dispatch(conn, info, params):
        if info.name == "HighlightElements":
            raise ArchicadUnavailableError("Tapir unavailable")
        return original_dispatch(conn, info, params)
    monkeypatch.setattr(archicad_mcp.scripting.apply, "_dispatch", mock_dispatch)

    store = ChangesetStore()
    ops = [_props(_write("w-1", "EI30")),
           {"kind": "command", "name": "HighlightElements", "params": {}},
           _props(_write("w-2", "EI90"))]
    cs = store.add(19723, "Test House", ops, [])
    out = run(store, cs.id)
    # First props op should succeed, command should fail with exception,
    # second props should not run.
    assert out["applied"] == 1
    assert out["commands"] == [{"name": "HighlightElements", "ok": False,
                                "code": None, "message": "Tapir unavailable"}]
    assert "stopped" in out and out["stopped"]["at"] == "HighlightElements"
    assert ("w-2", "pid-OFFICE/Fire Rating") not in model.values


def test_apply_command_not_in_registry(world):
    model, _, _, run = world
    store = ChangesetStore()
    ops = [_props(_write("w-1", "EI30")),
           {"kind": "command", "name": "NonexistentCommand", "params": {}},
           _props(_write("w-2", "EI90"))]
    cs = store.add(19723, "Test House", ops, [])
    out = run(store, cs.id)
    assert out["applied"] == 1
    assert out["commands"] == [{"name": "NonexistentCommand", "ok": False,
                                "code": None,
                                "message": "command is not in this server's registry"}]
    assert "stopped" in out and out["stopped"]["at"] == "NonexistentCommand"
    assert ("w-2", "pid-OFFICE/Fire Rating") not in model.values


# ---------- partial progress, command outcomes, identity ----------

def test_apply_keeps_the_progress_of_batches_sent_before_a_failed_one(world):
    from multiconn_archicad.errors import StandardAPIError
    model, core, _, run = world
    batches = []

    def set_then_fail(params):
        batches.append(params)
        if len(batches) == 2:
            raise StandardAPIError(message="Invalid program status", code=4001)
        return model.set(params)

    core.official_responses["API.SetPropertyValuesOfElements"] = set_then_fail
    store = ChangesetStore()
    writes = [_write(f"g-{i}", f"x{i}") for i in range(1000)]
    cs = store.add(19723, "Test House", [_props(*writes)], [])
    out = run(store, cs.id)
    assert out["applied"] == 500
    assert out["stopped"] == {"at": "property writes", "code": 4001,
                              "message": "Invalid program status"}
    # Only the batch that was sent is read back, and it all reads back.
    assert out["mismatched"] == []
    reads = [p for c, p in core.calls if c == "API.GetPropertyValuesOfElements"]
    read_guids = {el["elementId"]["guid"] for p in reads for el in p["elements"]}
    assert read_guids == {f"g-{i}" for i in range(500)}


def test_a_property_written_twice_is_read_back_against_the_last_value(world):
    model, _, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House",
                   [_props(_write("w-1", "EI30"), _write("w-1", "EI90"))], [])
    out = run(store, cs.id)
    assert out["applied"] == 2
    assert out["mismatched"] == []


def test_command_stops_share_one_shape(world):
    _, _, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [
        {"kind": "command", "name": "NonexistentCommand", "params": {}}], [])
    out = run(store, cs.id)
    assert out["stopped"] == {"at": "NonexistentCommand", "code": None,
                              "message": "command is not in this server's registry"}
    cs = store.add(19723, "Test House", [
        {"kind": "command", "name": "HighlightElements", "params": {}}], [])
    out = run(store, cs.id)
    assert out["stopped"] == {"at": "HighlightElements", "code": None,
                              "message": "FakeCore: no canned response for "
                                         "HighlightElements"}


def test_per_element_command_failures_are_reported_without_stopping(world):
    model, core, _, run = world
    refusals = [{"success": False, "error": {"code": 6001, "message": f"denied {i}"}}
                for i in range(7)]
    core.tapir_responses["SetGDLParametersOfElements"] = {
        "executionResults": [{"success": True}, *refusals]}
    store = ChangesetStore()
    ops = [{"kind": "command", "name": "SetGDLParametersOfElements", "params": {}},
           _props(_write("w-2", "EI90"))]
    cs = store.add(19723, "Test House", ops, [])
    out = run(store, cs.id)
    assert out["commands"] == [{"name": "SetGDLParametersOfElements", "ok": False,
                                "failed": 7, "sample": refusals[:5]}]
    assert "stopped" not in out
    assert out["applied"] == 1  # the write after the command still ran


def test_a_command_whose_elements_all_succeed_reports_zero_failed(world):
    _, core, _, run = world
    core.tapir_responses["SetGDLParametersOfElements"] = {
        "executionResults": [{"success": True}, {"success": True}]}
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [
        {"kind": "command", "name": "SetGDLParametersOfElements", "params": {}}], [])
    out = run(store, cs.id)
    assert out["commands"] == [{"name": "SetGDLParametersOfElements", "ok": True,
                                "failed": 0}]


IDENTITY = {"name": "Test House", "is_teamwork": False,
            "location": "/Users/tester/Test House.pln"}


def test_apply_refuses_a_same_name_project_at_another_location(world):
    _, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [],
                   identity=dict(IDENTITY))
    core.tapir_responses["GetProjectInfo"] = {
        "projectName": "Test House", "isUntitled": False, "isTeamwork": False,
        "projectLocation": "/Users/tester/scratch/Test House.pln",
        "projectPath": "/Users/tester/scratch/Test House.pln"}
    out = run(store, cs.id)
    assert "Nothing was written" in out["error"] and "location" in out["error"]
    assert _sets(core) == []
    assert store.lookup(cs.id) is cs  # nothing ran, so it can still be applied


def test_apply_refuses_when_teamwork_state_differs(world):
    _, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [],
                   identity=dict(IDENTITY))
    core.tapir_responses["GetProjectInfo"] = {
        "projectName": "Test House", "isTeamwork": True,
        "projectLocation": "teamwork://u:eyJhbGciOiJI.eyJzdWIiOiIx.sig@host/"
                           "Users/tester/Test House.pln"}
    out = run(store, cs.id)
    assert "Nothing was written" in out["error"]
    assert "eyJ" not in out["error"]  # the token never reaches the reply
    assert _sets(core) == []


def test_apply_proceeds_when_the_identity_matches(world):
    _, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [],
                   identity=dict(IDENTITY))
    out = run(store, cs.id)
    assert out["applied"] == 1


def test_project_identity_strips_the_teamwork_token():
    from archicad_mcp.core.project import project_identity
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir={
        **api_replays.TAPIR, "GetProjectInfo": {
            "projectName": "CVP", "isTeamwork": True,
            "projectLocation": "teamwork://ales:eyJhbGciOiJI.eyJzdWIiOiIx.sig"
                               "@bimcloud.example:22000/Projects/CVP?token=abc"}})
    identity = project_identity(ArchicadConnection(19723, core=core))
    assert identity == {"name": "CVP", "is_teamwork": True,
                        "location": "teamwork://bimcloud.example:22000/Projects/CVP"}


def test_project_identity_is_none_without_tapir():
    from archicad_mcp.core.project import project_identity
    official = dict(api_replays.OFFICIAL)
    official["API.IsAddOnCommandAvailable"] = {"available": False}
    core = FakeCore(official=official, tapir={})
    assert project_identity(ArchicadConnection(19723, core=core)) is None


def test_summary_says_whether_the_project_is_teamwork():
    cs = ChangesetStore().add(19723, "Test House", [], [], identity=dict(IDENTITY))
    assert summarize(cs)["teamwork"] is False
    assert summarize(ChangesetStore().add(19723, "P", [], []))["teamwork"] is None


# ---------- single use under concurrency ----------

def test_take_marks_applied_and_a_second_take_refuses():
    store = ChangesetStore()
    cs = store.add(19723, "P", [], [])
    assert store.take(cs.id) is cs and cs.applied
    with pytest.raises(ChangesetError, match="already applied"):
        store.take(cs.id)


def test_concurrent_adds_and_lookups_do_not_break_the_store():
    import sys
    import threading
    store = ChangesetStore(capacity=5)
    errors = []

    def churn():
        try:
            for _ in range(2000):
                cs = store.add(19723, "P", [], [])
                try:
                    store.lookup(cs.id)
                except ChangesetError:
                    pass  # evicted by another thread: fine
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    # fastmcp runs sync tools on a threadpool. A tiny switch interval makes
    # the interleaving that broke eviction and expiry happen within the test.
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=churn) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(interval)
    assert errors == []


def test_skipped_changes_are_grouped_by_reason():
    # Live 23.09.2026: 113 hotlinked doors produced 20 samples repeating one
    # long reason, 7k characters of preview saying one thing.
    skipped = ([{"guid": f"h-{i}", "property": "P", "reason": "hotlinked"}
                for i in range(113)]
               + [{"guid": f"v-{i}", "property": "P", "reason": f"value {i}"}
                  for i in range(12)])
    groups, not_shown = group_skipped(skipped)
    assert groups[0] == {"reason": "hotlinked", "count": 113,
                         "sample": [{"guid": f"h-{i}", "property": "P"}
                                    for i in range(5)]}
    assert len(groups) == 10 and not_shown == 3


def test_summary_reports_how_many_reasons_were_left_out():
    skipped = [{"guid": f"v-{i}", "property": "P", "reason": f"value {i}"}
               for i in range(12)]
    summary = summarize(ChangesetStore().add(19723, "P", [], skipped))
    assert summary["skipped"] == 12
    assert len(summary["skipped_reasons"]) == 10
    assert summary["skipped_reasons_not_shown"] == 2
