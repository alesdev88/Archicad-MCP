"""Changesets: stored plans that apply once, exactly as previewed."""
import pytest

from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.apply import apply_changeset
from archicad_mcp.scripting.changesets import ChangesetError, ChangesetStore, summarize
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
    assert summary["skipped"] == 1 and summary["skipped_sample"] == skipped
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
    assert out["failed"] == [{"guid": "w-1", "property": "OFFICE/Fire Rating",
                              "code": 6001, "message": "TeamWork permission denied"}]
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


def test_apply_caps_long_reports(world):
    model, _, _, run = world
    guids = [f"g-{i}" for i in range(60)]
    model.refuse.update(guids)
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(*[_write(g, "x") for g in guids])], [])
    out = run(store, cs.id)
    assert len(out["failed"]) == 50
    assert out["failed_not_shown"] == 10
