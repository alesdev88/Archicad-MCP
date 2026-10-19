"""delete_elements / move_elements against a project with more than one database.

Verified live 28.09.2026 (AC 29/5101, Tapir 1.5.10, a Teamwork project): with a
Layout window active, DeleteElements on 12 reserved floor-plan labels answered
success and deleted nothing, and the tool reported {"deleted": 12}. Archicad
changes only elements in the database of the active window; FilterElements
IsEditable had said 0 of 12 beforehand. With the floor plan active the same
call deleted them.

The fake below behaves that way: every Tapir element command acts on the
active window's database only, and DeleteElements answers success whatever it
did. The official GetTypesOfElements answers 7203 "Element not supported" for
a label, as it did live for every label on 28.09.2026, so it cannot say
whether one exists; Tapir GetDetailsOfElements can.

IsEditable is not a database check in one direction: live on 19.10.2026, from
the floor plan, it passed a Text in a layout database and a Line in a section
database. The fake passes every database's elements from the floor plan. Its
GetDetailsOfElements still finds only the active database's elements, which
holds from a Layout (28.09.2026) but not from the floor plan: there, live on
19.10.2026, it found layout Drawings and section Objects too
(docs/known-issues.md). The other-database tests cover the elements it misses.

Why a locked element is locked comes from reads IsEditable does not make: the
details' hotlinkId and layerIndex, the layer table (recorded on MCP-Test) and
the built-in property General_Locked.
"""
import pytest
from multiconn_archicad.errors import StandardAPIError

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import (
    ELEMENT_LOCK_REASON,
    HOTLINK_REASON,
    layer_locked_reason,
)
from archicad_mcp.core.mutate import delete_elements, move_elements
from tests.conftest import FakeCore
from tests.fixtures import api_replays
from tests.fixtures.modify_elements import layers_answer, load

BAD_ID = -2130313112
LAYER = 33          # "Structural - Bearing" in the recorded layer list
LOCKED_LAYER = 2    # "Interior - Partition", locked in this fake


class Project:
    def __init__(self, floor_plan=(), layout=(), section=(), window="FloorPlan",
                 locked=(), teamwork=False, mine=None, hidden=(),
                 details_take_fields=True, hotlinked=(), on_locked_layer=(),
                 element_locked=()):
        self.databases = {"FloorPlan": set(floor_plan), "Layout": set(layout),
                          "Section": set(section)}
        self.window = window
        self.hotlinked = set(hotlinked)              # inside a hotlinked module
        self.on_locked_layer = set(on_locked_layer)
        self.element_locked = set(element_locked)    # Edit > Locking
        # Not editable: for a reason the reads can name, or (`locked`) not.
        self.locked = set(locked).union(hotlinked, on_locked_layer, element_locked)
        self.hidden = set(hidden)          # on a layer hidden in the active view
        self.details_take_fields = details_take_fields  # Tapir 1.5.9+
        self.teamwork = teamwork
        self.mine = set(mine) if mine is not None else self.everywhere()
        self.survivors: set[str] = set()   # reported deleted, still there
        self.delete_result = {"success": True}
        self.delete_raises = None
        self.move_errors: dict[str, dict] = {}
        self.move_results = None           # override the executionResults list
        self.moved: list[str] = []

    @property
    def active(self) -> set[str]:
        return self.databases[self.window]

    def everywhere(self) -> set[str]:
        return set().union(*self.databases.values())

    def _editable(self, guid):
        reach = self.everywhere() if self.window == "FloorPlan" else self.active
        return (guid in reach and guid not in self.locked
                and guid not in self.hidden
                and (not self.teamwork or guid in self.mine))

    def filter_elements(self, p):
        tests = {"IsEditable": self._editable,
                 "IsVisibleByLayer": lambda g: g in self.active and g not in self.hidden,
                 "InMyWorkspace": lambda g: g in self.active and g in self.mine}
        [name] = p["filters"]
        return {"elements": [e for e in p["elements"]
                             if tests[name](e["elementId"]["guid"])]}

    def types(self, p):
        return {"typesOfElements": [
            {"error": {"code": 7203, "message": "Element not supported"}}
            for _ in p["elements"]]}

    def _detail(self, guid, fields):
        item = {"type": "Label", "floorIndex": 0,
                "layerIndex": LOCKED_LAYER if guid in self.on_locked_layer else LAYER}
        if guid in self.hotlinked:
            item["hotlinkId"] = {"guid": "HOTLINK-1"}
        return item if fields is None else {k: v for k, v in item.items() if k in fields}

    def details(self, p):
        if "fields" in p and not self.details_take_fields:
            raise StandardAPIError(message="Invalid parameters", code=None)
        return {"detailsOfElements": [
            self._detail(e["elementId"]["guid"], p.get("fields"))
            if e["elementId"]["guid"] in self.active
            else {"error": {"code": BAD_ID,
                            "message": "Failed to get the details of element"}}
            for e in p["elements"]]}

    def property_values(self, p):
        return {"propertyValuesForElements": [
            {"propertyValues": [
                {"propertyValue": {"type": "boolean", "status": "normal",
                                   "value": e["elementId"]["guid"] in self.element_locked}}
                if prop["propertyId"]["guid"] == "pid-General_Locked"
                else {"error": {"code": 1, "message": "n/a"}}
                for prop in p["properties"]]}
            for e in p["elements"]]}

    def delete(self, p):
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            if guid not in self.survivors:
                self.active.discard(guid)
        if self.delete_raises is not None:
            raise self.delete_raises
        return self.delete_result

    def move(self, p):
        results = []
        for item in p["elementsWithMoveVectors"]:
            guid = item["elementId"]["guid"]
            if guid in self.move_errors:
                results.append({"success": False, "error": self.move_errors[guid]})
            else:
                self.moved.append(guid)
                results.append({"success": True})
        return {"executionResults": self.move_results
                if self.move_results is not None else results}

    def connect(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.GetTypesOfElements"] = self.types
        official["API.GetPropertyValuesOfElements"] = self.property_values
        tapir = dict(api_replays.TAPIR)
        tapir.update({
            "GetAttributesByType": load("layers_by_type.json"),
            "GetLayers": layers_answer(locked={LOCKED_LAYER}),
            "FilterElements": self.filter_elements,
            "GetDetailsOfElements": self.details,
            "DeleteElements": self.delete,
            "MoveElements": self.move,
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
            "GetProjectInfo": {**api_replays.TAPIR["GetProjectInfo"],
                               "isTeamwork": self.teamwork},
        })
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def sent(self, command: str) -> list[dict]:
        return [params for name, params in self.core.calls if name == command]


LABELS = [f"label-{i}" for i in range(12)]
VECTOR = {"x": 1.0, "y": 0.0, "z": 0.0}


def _only_group(result: dict, key: str) -> dict:
    [group] = result[key]
    return group


# ---------- delete ----------

def test_delete_from_a_layout_deletes_nothing_and_says_why():
    # The live case: reserved floor-plan labels, Layout window active.
    project = Project(floor_plan=LABELS, window="Layout", teamwork=True)
    out = delete_elements(project.connect(), LABELS, confirm=True)
    assert out["requested"] == 12
    assert out["deleted"] == 0
    group = _only_group(out, "not_deleted")
    assert group["count"] == 12 and group["guids"] == LABELS
    assert "Layout" in group["reason"] and "floor plan" in group["reason"]
    assert out["active_window"] == "Layout"
    assert project.sent("DeleteElements") == []
    assert project.databases["FloorPlan"] == set(LABELS)


def test_delete_with_the_floor_plan_active_reports_what_is_gone():
    project = Project(floor_plan=LABELS, teamwork=True)
    out = delete_elements(project.connect(), LABELS, confirm=True)
    assert out == {"requested": 12, "deleted": 12}
    assert project.databases["FloorPlan"] == set()


def test_delete_counts_only_elements_that_are_gone_afterwards():
    # Archicad answers success for the batch; the count must come from a read.
    project = Project(floor_plan=["a", "b", "c"])
    project.survivors = {"b"}
    out = delete_elements(project.connect(), ["a", "b", "c"], confirm=True)
    assert out["deleted"] == 2
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"]
    assert "still" in group["reason"]


def test_delete_passes_on_archicads_refusal():
    project = Project(floor_plan=["a", "b"])
    project.survivors = {"a", "b"}
    project.delete_result = {"success": False,
                             "error": {"code": 6001, "message": "denied"}}
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 0
    group = _only_group(out, "not_deleted")
    assert group["count"] == 2 and "denied" in group["reason"]


def test_delete_skips_an_unreserved_teamwork_element():
    project = Project(floor_plan=["a", "b"], teamwork=True, mine={"a"})
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"]
    assert "not reserved" in group["reason"] and "reserve_elements" in group["reason"]
    [params] = project.sent("DeleteElements")
    assert params == {"elements": [{"elementId": {"guid": "a"}}]}


def test_delete_skips_an_element_inside_a_hotlink():
    project = Project(floor_plan=["a", "b"], hotlinked={"b"})
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"] and group["reason"] == HOTLINK_REASON
    assert out["active_window"] == "FloorPlan"


def test_delete_names_an_element_on_a_hidden_layer():
    # Live 28.09.2026: labels on a layer the working view kept off were not
    # editable until the layer was turned on.
    project = Project(floor_plan=["a", "b"], hidden={"b"}, teamwork=True)
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"] and "hidden layer" in group["reason"]


def test_delete_works_with_a_tapir_that_predates_the_fields_filter():
    project = Project(floor_plan=["a", "b"], locked={"b"}, details_take_fields=False)
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 1
    assert "hotlinked module" in _only_group(out, "not_deleted")["reason"]


def test_delete_names_an_unknown_guid_as_not_found():
    project = Project(floor_plan=["a"])
    out = delete_elements(project.connect(), ["a", "nope"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["nope"] and "does not exist" in group["reason"]


def test_delete_counts_each_guid_once():
    project = Project(floor_plan=["a"])
    out = delete_elements(project.connect(), ["a", "a"], confirm=True)
    assert out == {"requested": 1, "deleted": 1}
    [params] = project.sent("DeleteElements")
    assert params == {"elements": [{"elementId": {"guid": "a"}}]}


def test_delete_lists_at_most_the_report_cap_per_reason():
    guids = [f"gone-{i}" for i in range(60)]
    out = delete_elements(Project().connect(), guids, confirm=True)
    group = _only_group(out, "not_deleted")
    assert group["count"] == 60
    assert group["guids"] == guids[:50] and group["not_shown"] == 10


def test_delete_still_reports_what_went_when_the_command_errors():
    # A transport error after Archicad acted: the read says what happened.
    project = Project(floor_plan=["a", "b"])
    project.delete_raises = StandardAPIError(message="timed out", code=None)
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 2
    assert out["stopped"] == {"code": None, "message": "timed out"}


def test_delete_refuses_without_confirm_and_touches_nothing():
    project = Project(floor_plan=["a"])
    out = delete_elements(project.connect(), ["a"])
    assert "confirm=true" in out["error"]
    assert project.core.calls == []


def test_delete_from_the_floor_plan_does_not_send_a_layout_text():
    # Live 19.10.2026: from the floor plan, IsEditable passed a Text in a
    # layout database. In this fake the reads do not find it, so a delete
    # that passes over it would count it as gone.
    project = Project(floor_plan=["a"], layout=["text"])
    out = delete_elements(project.connect(), ["a", "text"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["text"] and group["reason"].startswith("not found")
    assert out["active_window"] == "FloorPlan"
    [params] = project.sent("DeleteElements")
    assert params == {"elements": [{"elementId": {"guid": "a"}}]}
    assert project.databases["Layout"] == {"text"}


# ---------- move ----------

def test_move_from_a_layout_moves_nothing_and_says_why():
    project = Project(floor_plan=["a", "b"], window="Layout")
    out = move_elements(project.connect(), ["a", "b"], VECTOR, confirm=True)
    assert out["requested"] == 2 and out["moved"] == 0
    group = _only_group(out, "not_moved")
    assert group["count"] == 2 and "Layout" in group["reason"]
    assert out["active_window"] == "Layout"
    assert project.sent("MoveElements") == []


def test_move_counts_archicads_per_element_results():
    project = Project(floor_plan=["a", "b"])
    project.move_errors = {"b": {"code": -2130313112, "message": "Failed to move"}}
    out = move_elements(project.connect(), ["a", "b"], VECTOR, confirm=True)
    assert out["moved"] == 1
    group = _only_group(out, "not_moved")
    assert group["guids"] == ["b"] and "Failed to move" in group["reason"]
    [params] = project.sent("MoveElements")
    assert params["elementsWithMoveVectors"] == [
        {"elementId": {"guid": "a"}, "moveVector": VECTOR},
        {"elementId": {"guid": "b"}, "moveVector": VECTOR}]


def test_move_does_not_count_an_element_archicad_gave_no_result_for():
    project = Project(floor_plan=["a", "b"])
    project.move_results = [{"success": True}]
    out = move_elements(project.connect(), ["a", "b"], VECTOR, confirm=True)
    assert out["moved"] == 1
    assert _only_group(out, "not_moved")["guids"] == ["b"]


def test_move_with_everything_editable():
    project = Project(floor_plan=["a", "b"])
    out = move_elements(project.connect(), ["a", "b", "a"], VECTOR, confirm=True)
    assert out == {"requested": 2, "moved": 2}
    assert project.moved == ["a", "b"]


def test_move_from_the_floor_plan_does_not_send_a_section_line():
    # Live 19.10.2026: from the floor plan, IsEditable passed a Line in a
    # section database.
    project = Project(floor_plan=["a"], section=["line"])
    out = move_elements(project.connect(), ["a", "line"], VECTOR, confirm=True)
    assert out["moved"] == 1
    group = _only_group(out, "not_moved")
    assert group["guids"] == ["line"] and group["reason"].startswith("not found")
    assert out["active_window"] == "FloorPlan"
    assert project.moved == ["a"]


def test_the_presence_check_reads_only_the_type():
    # GetDetailsOfElements without `fields` builds every element's floor-plan
    # polygons; the check needs only whether an entry comes back.
    project = Project(floor_plan=["a", "b"], section=["line"])
    move_elements(project.connect(), ["a", "b", "line"], VECTOR, confirm=True)
    reads = project.sent("GetDetailsOfElements")
    assert reads and all(p["fields"] == ["type"] for p in reads)


def test_move_refuses_without_confirm_and_touches_nothing():
    project = Project(floor_plan=["a"])
    out = move_elements(project.connect(), ["a"], VECTOR)
    assert "confirm=true" in out["error"]
    assert project.core.calls == []


# ---------- why a locked element is locked ----------

def test_move_names_the_locked_layer():
    project = Project(floor_plan=["a", "b"], on_locked_layer={"b"})
    out = move_elements(project.connect(), ["a", "b"], VECTOR, confirm=True)
    group = _only_group(out, "not_moved")
    assert group["guids"] == ["b"]
    assert group["reason"] == layer_locked_reason("Interior - Partition")


def test_delete_names_an_element_lock():
    project = Project(floor_plan=["a", "b"], element_locked={"b"})
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"] and group["reason"] == ELEMENT_LOCK_REASON


def _reasons(out: dict, key: str) -> dict[str, str]:
    return {g: group["reason"] for group in out[key] for g in group["guids"]}


def test_the_element_lock_is_read_only_where_nothing_else_explains_the_lock():
    # General_Locked is a property read, the read class that has crashed
    # Archicad: only the locked elements without a hotlink or a locked layer.
    project = Project(floor_plan=["a", "h", "l", "e"], hotlinked={"h"},
                      on_locked_layer={"l"}, element_locked={"e"})
    out = move_elements(project.connect(), ["a", "h", "l", "e"], VECTOR, confirm=True)
    assert _reasons(out, "not_moved") == {
        "h": HOTLINK_REASON, "l": layer_locked_reason("Interior - Partition"),
        "e": ELEMENT_LOCK_REASON}
    [read] = project.sent("API.GetPropertyValuesOfElements")
    assert read["elements"] == [{"elementId": {"guid": "e"}}]


def test_no_lock_is_read_without_a_locked_element():
    project = Project(floor_plan=["a", "b"], layout=["c"], hidden={"b"},
                      teamwork=True, mine={"a", "b"})
    delete_elements(project.connect(), ["a", "b", "c", "nope"], confirm=True)
    for command in ("GetLayers", "API.GetPropertyValuesOfElements"):
        assert project.sent(command) == []
    assert all(p["fields"] == ["type"] for p in project.sent("GetDetailsOfElements"))


def test_without_getlayers_the_other_locks_are_still_named():
    project = Project(floor_plan=["h", "l", "e"], hotlinked={"h"},
                      on_locked_layer={"l"}, element_locked={"e"})
    conn = project.connect()
    del project.core.tapir_responses["GetLayers"]
    out = delete_elements(conn, ["h", "l", "e"], confirm=True)
    reasons = _reasons(out, "not_deleted")
    assert reasons["h"] == HOTLINK_REASON and reasons["e"] == ELEMENT_LOCK_REASON
    assert reasons["l"].startswith("not editable: it is inside a hotlinked module")


@pytest.mark.parametrize("window", ["FloorPlan", None])
def test_reasons_leave_the_window_out_when_it_is_not_the_suspect(window):
    # On the floor plan, or when the window type cannot be read, the reason is
    # the plain Teamwork / hotlink one.
    project = Project(floor_plan=["a"], locked={"a"})
    conn = project.connect()
    if window is None:
        del project.core.tapir_responses["GetCurrentWindowType"]
    out = delete_elements(conn, ["a"], confirm=True)
    reason = _only_group(out, "not_deleted")["reason"]
    assert reason.startswith("not editable: it is inside a hotlinked module")
    assert out.get("active_window") == window
