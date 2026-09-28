"""delete_elements / move_elements against a project with more than one database.

Verified live 28.09.2026 (AC 29/5101, Tapir 1.5.10, a Teamwork project): with a
Layout window active, DeleteElements on 12 reserved floor-plan labels answered
success and deleted nothing, and the tool reported {"deleted": 12}. Archicad
changes only elements in the database of the active window; FilterElements
IsEditable had said 0 of 12 beforehand. With the floor plan active the same
call deleted them.

The fake below behaves that way: every Tapir element command acts on the
active window's database only, and DeleteElements answers success whatever it
did. The official GetTypesOfElements sees every database here, the harder case
for telling a caller why an element was not deleted.
"""
import pytest
from multiconn_archicad.errors import StandardAPIError

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.mutate import delete_elements, move_elements
from tests.conftest import FakeCore
from tests.fixtures import api_replays

BAD_ID = -2130313112


class Project:
    def __init__(self, floor_plan=(), layout=(), window="FloorPlan", locked=(),
                 teamwork=False, mine=None):
        self.databases = {"FloorPlan": set(floor_plan), "Layout": set(layout)}
        self.window = window
        self.locked = set(locked)
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
        return (guid in self.active and guid not in self.locked
                and (not self.teamwork or guid in self.mine))

    def filter_elements(self, p):
        tests = {"IsEditable": self._editable,
                 "InMyWorkspace": lambda g: g in self.active and g in self.mine}
        [name] = p["filters"]
        return {"elements": [e for e in p["elements"]
                             if tests[name](e["elementId"]["guid"])]}

    def types(self, p):
        return {"typesOfElements": [
            {"typeOfElement": {"elementId": e["elementId"], "elementType": "Label"}}
            if e["elementId"]["guid"] in self.everywhere()
            else {"error": {"code": BAD_ID, "message": "The element does not exist."}}
            for e in p["elements"]]}

    def details(self, p):
        return {"detailsOfElements": [
            {"type": "Label", "floorIndex": 0} if e["elementId"]["guid"] in self.active
            else {"error": {"code": BAD_ID,
                            "message": "Failed to get the details of element"}}
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
        tapir = dict(api_replays.TAPIR)
        tapir.update({
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
    project = Project(floor_plan=["a", "b"], locked={"b"})
    out = delete_elements(project.connect(), ["a", "b"], confirm=True)
    assert out["deleted"] == 1
    group = _only_group(out, "not_deleted")
    assert group["guids"] == ["b"] and "hotlinked module" in group["reason"]
    assert out["active_window"] == "FloorPlan"


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


def test_move_refuses_without_confirm_and_touches_nothing():
    project = Project(floor_plan=["a"])
    out = move_elements(project.connect(), ["a"], VECTOR)
    assert "confirm=true" in out["error"]
    assert project.core.calls == []


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
