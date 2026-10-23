"""learn_library_part: samples placed past the model, measured, captured and deleted again."""
from __future__ import annotations

import math
from datetime import date

import pytest
from multiconn_archicad.errors import TapirCommandError
from PIL import Image

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.part_learning import (
    BOX_CHUNK,
    CLEARANCE,
    SAMPLE_HEIGHT,
    SAMPLE_SIZES,
    SPACING,
    learn_library_part,
    list_library_part_records,
    record_library_part,
    sample_origin,
)
from archicad_mcp.core.part_records import COMBOS, PartRecord, PartRecords
from archicad_mcp.tapir_requirements import TOOL_COMMANDS
from tests.conftest import FakeCore
from tests.fixtures import api_replays

# Document name -> main GUID of the loaded parts.
LIBRARY = {
    "Bed Double 01": "6B4F1A52-0C3E-4D1B-9A77-2E5C8D90F311",
    "Chair 01": "1F0C2D3E-4A5B-4C6D-8E7F-901A2B3C4D5E",
    "Window": "0D93E7B8-5A41-4C2F-8E16-71B3A4C5D622",
    "Double Window": "7A1B2C3D-4E5F-4061-8273-8495A6B7C8D9",
    "Door": "C0FFEE00-1234-4567-89AB-CDEF01234567",
}


def corner_boxes(min_a: float = 0.0, min_b: float = 0.0):
    """A part inserted at its foot-left corner that draws A along x and B along y, never
    smaller than min_a and min_b (Bed Double 01 takes B 1.2 when sent 1.0)."""
    def draw(a: float, b: float):
        a, b = max(a, min_a), max(b, min_b)
        return (a, b), (0.0, 0.0, a, b)
    return draw


class Fake:
    """A floor plan with the user's elements, the loaded parts, and what learning places.

    `fail_create_at` is the index of the library-part sample (the temporary wall not
    counted) whose create answers an error; `keep` holds indices into created_guids
    that DeleteElements refuses; `create_raises` places the first sample of a create
    and then fails the whole command, so no reply names it; `appear` is (type, box)
    of elements the user adds while the plan is zoomed (FitInWindow).
    """

    def __init__(self, boxes=None, boxes_of_model=None, model_elements=0, window="FloorPlan",
                 tapir_version="1.7.2", library=None, fail_create_at=None, gdl_fails=False,
                 refuse_parameters=(), keep=(), capture=True, create_raises=False,
                 mirror_axis="x", act_story=0, stories=None, appear=(), rotation=0.0,
                 wall_flipped=True):
        self.draw = boxes or corner_boxes()
        self.mirror_axis = mirror_axis       # the local axis `reflected` negates
        self.model: dict[str, tuple] = {}
        self.model_types: dict[str, str] = {}
        if boxes_of_model is not None:
            self.model["model-0"] = tuple(float(v) for v in boxes_of_model)
        for i in range(model_elements):
            self.model[f"model-{i}"] = (float(i), 0.0, i + 1.0, 1.0)
        self.act_story = act_story
        self.stories = [{"index": i, "floorId": i + 1, "dispOnSections": True,
                         "level": 3.0 * i, "height": 3.0, "name": f"Story {i}"}
                        for i in range(3)] if stories is None else stories
        self.appear = list(appear)
        self.rotation = rotation             # the plan view's, in radians; None: not said
        # A temporary wall's flipped: the Wall tool's default gave true live (probe);
        # None leaves it out of the wall's details.
        self.wall_flipped = wall_flipped
        self.window = window
        self.tapir_version = tapir_version
        self.library = dict(LIBRARY if library is None else library)
        self.fail_create_at = fail_create_at
        self.gdl_fails = gdl_fails
        self.refuse_parameters = set(refuse_parameters)
        self.keep = set(keep)
        self.capture = capture
        self.create_raises = create_raises
        self.elements: dict[str, dict] = {}     # placed and not deleted yet
        self.created_guids: list[str] = []
        self.deleted: list[str] = []
        self.deleted_types: list[str] = []
        self.box_calls = 0
        self.samples_made = 0
        self.sent: dict[str, list[dict]] = {}

    @property
    def remaining(self) -> list[str]:
        return list(self.elements)

    def _place(self, element_type: str, item: dict) -> str:
        guid = f"5A0E{len(self.created_guids) + 1:04X}-0000-4000-8000-000000000000"
        host = (item.get("ownerWallId") or {}).get("guid")
        self.elements[guid] = {"type": element_type, "item": item, "host": host}
        self.created_guids.append(guid)
        return guid

    def _creator(self, command: str, element_type: str):
        def create(p):
            [items] = p.values()
            self.sent.setdefault(command, []).append(p)
            out = []
            for item in items:
                if element_type == "Wall":
                    out.append({"elementId": {"guid": self._place("Wall", item)}})
                    continue
                index, self.samples_made = self.samples_made, self.samples_made + 1
                if index == self.fail_create_at:
                    out.append({"error": {"code": -2130313215,
                                          "message": "Failed to create the element"}})
                    continue
                guid = self._place(element_type, item)
                if self.create_raises:
                    raise TapirCommandError(message="The command stopped", code=None)
                out.append({"elementId": {"guid": guid}})
            return {"elements": out}
        return create

    def object_box(self, item: dict) -> tuple:
        """The plan box an object item is drawn with, in world coordinates."""
        _, (x0, y0, x1, y1) = self.draw(item["dimensions"]["x"], item["dimensions"]["y"])
        if item.get("reflected") and self.mirror_axis == "x":
            x0, x1 = -x1, -x0
        elif item.get("reflected"):
            y0, y1 = -y1, -y0
        x, y = item["coordinates"]["x"], item["coordinates"]["y"]
        return (x + x0, y + y0, x + x1, y + y1)

    def _box(self, guid: str):
        if guid in self.model:
            return self.model[guid]
        element = self.elements.get(guid)
        if element is None:
            return None
        item = element["item"]
        if element["type"] in ("Object", "Lamp"):
            return self.object_box(item)
        if element["type"] == "Wall":
            begin, end = item["begCoordinate"], item["endCoordinate"]
            return (begin["x"], begin["y"], end["x"], end["y"] + 0.3)
        # An opening in a wall along +X: its width about the centre, the leaf's reach.
        begin = self.elements[element["host"]]["item"]["begCoordinate"]
        x = begin["x"] + item["centerOffset"]
        return (x - item["width"] / 2, begin["y"] - 1.0, x + item["width"] / 2, begin["y"] + 0.3)

    def _boxes(self, p):
        self.box_calls += 1
        out = []
        for e in p["elements"]:
            box = self._box(e["elementId"]["guid"])
            out.append({"boundingBox2D": dict(zip(("xMin", "yMin", "xMax", "yMax"), box))}
                       if box else {"error": {"code": 7204, "message": "Element not found"}})
        return {"boundingBoxes2D": out}

    def _gdl(self, p):
        out = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            element = self.elements.get(guid)
            if element is None or self.gdl_fails or element["type"] in ("Door", "Window"):
                out.append({"error": {"code": -2130313215, "message":
                                      f"Failed to get parameters of element with guid {guid}!"}})
                continue
            item = element["item"]
            (a, b), _ = self.draw(item["dimensions"]["x"], item["dimensions"]["y"])
            out.append({"parameters": [
                {"name": "A", "displayName": "Width", "index": 0, "type": "Length", "value": a},
                {"name": "B", "displayName": "Length", "index": 1, "type": "Length", "value": b},
                {"name": "ZZYZX", "displayName": "Height", "index": 2, "type": "Length",
                 "value": 0.66},
                {"name": "gs_cont_pen", "displayName": "Contour Pen", "index": 3,
                 "type": "PenColor", "value": 1}]})
        return {"gdlParametersOfElements": out}

    def _set_gdl(self, p):
        self.sent.setdefault("SetGDLParametersOfElements", []).append(p)
        results = []
        for entry in p["elementsWithGDLParameters"]:
            refused = [x["name"] for x in entry["gdlParameters"]
                       if x["name"] in self.refuse_parameters]
            results.append({"success": False, "error": {
                "code": -2130313215, "message": f"Invalid parameter name: {refused[0]}"}}
                if refused else {"success": True})
        return {"executionResults": results}

    def _delete(self, p):
        self.sent.setdefault("DeleteElements", []).append(p)
        kept = {self.created_guids[i] for i in self.keep}
        results = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            if guid in self.model and guid not in kept:     # the user's: Archicad obeys
                self.deleted.append(guid)
                del self.model[guid]
                results.append({"success": True})
                continue
            element = self.elements.get(guid)
            if guid in kept or element is None:
                results.append({"success": False, "error": {
                    "code": -2130313112, "message": "The element cannot be deleted"}})
                continue
            self.deleted.append(guid)
            self.deleted_types.append(element["type"])
            del self.elements[guid]
            # As in Archicad, a wall takes the openings it hosts with it.
            for hosted in [g for g, el in self.elements.items() if el["host"] == guid]:
                del self.elements[hosted]
            results.append({"success": True})
        return {"executionResults": results}

    def _details(self, p):
        out = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            if guid in self.elements:
                entry = {"type": self.elements[guid]["type"]}
                if entry["type"] == "Wall":
                    entry["details"] = {"geometryType": "Straight", "arcAngle": 0}
                    if self.wall_flipped is not None:
                        entry["details"]["flipped"] = self.wall_flipped
                out.append(entry)
            elif guid in self.model:
                out.append({"type": self.model_types.get(guid, "Slab")})
            else:
                out.append({"error": {"code": -2130313112, "message": "not found"}})
        return {"detailsOfElements": out}

    def _save(self, p):
        if self.capture:
            Image.new("RGB", (640, 360), "white").save(p["imageFilePath"], "PNG")
        reply = {"imageFilePath": p["imageFilePath"],
                 "zoom": {"xMin": 0.0, "yMin": -1.0, "xMax": 20.0, "yMax": 4.0}}
        if self.rotation is not None:
            reply["rotation"] = self.rotation
        return reply

    def _fit(self, p):
        self.sent.setdefault("FitInWindow", []).append(p)
        for n, (element_type, box) in enumerate(self.appear):
            self.model[f"user-{n}"] = tuple(float(v) for v in box)
            self.model_types[f"user-{n}"] = element_type
        return {"success": True}

    def _types(self, p):
        out = []
        for e in p["elements"]:
            guid = e["elementId"]["guid"]
            element_type = self.model_types.get(guid) or (self.elements.get(guid) or {}).get(
                "type") or ("Slab" if guid in self.model else None)
            out.append({"typeOfElement": {"elementId": e["elementId"],
                                          "elementType": element_type}}
                       if element_type else {"error": {"code": 7204,
                                                       "message": "Element not found"}})
        return {"typesOfElements": out}

    def connection(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.Get2DBoundingBoxes"] = self._boxes
        official["API.GetTypesOfElements"] = self._types
        tapir = {
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
            "GetStories": lambda p: {"firstStory": 0, "lastStory": 2,
                                     "actStory": self.act_story, "skipNullFloor": False,
                                     "stories": self.stories},
            "GetAddOnVersion": {"version": self.tapir_version},
            "GetAllElements": lambda p: {"elements": [
                {"elementId": {"guid": g}} for g in [*self.model, *self.elements]]},
            "GetAvailableLibraryParts": lambda p: {"libraryParts": [
                {"guid": guid, "index": i, "documentName": name, "fileName": f"{name}.gsm",
                 "typeId": "Object"}
                for i, (name, guid) in enumerate(self.library.items(), 1)],
                "skippedCount": 0},
            "CreateObjects": self._creator("CreateObjects", "Object"),
            "CreateLamps": self._creator("CreateLamps", "Lamp"),
            "CreateWalls": self._creator("CreateWalls", "Wall"),
            "CreateWindows": self._creator("CreateWindows", "Window"),
            "CreateDoors": self._creator("CreateDoors", "Door"),
            "GetGDLParametersOfElements": self._gdl,
            "SetGDLParametersOfElements": self._set_gdl,
            "DeleteElements": self._delete,
            "GetDetailsOfElements": self._details,
            "FitInWindow": self._fit,
            "SaveCurrentWindowAsImage": self._save,
        }
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def items(self, command: str) -> list[dict]:
        return [item for p in self.sent.get(command, []) for item in next(iter(p.values()))]


@pytest.fixture
def records(tmp_path) -> PartRecords:
    return PartRecords(tmp_path / "r.json")


def test_object_samples_are_measured_and_all_deleted(tmp_path):
    fake = Fake(boxes=corner_boxes())
    out = learn_library_part(fake.connection(), PartRecords(tmp_path / "r.json"),
                             "Bed Double 01", "object")
    assert out["measured"]["xMax"] == pytest.approx([1.0, 0.0])
    assert fake.deleted == fake.created_guids and fake.remaining == []
    assert out["png"].startswith(b"\x89PNG") and len(out["samples"]) == 3


def test_the_measured_record_is_stored_and_says_what_is_still_needed(records):
    out = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    assert "error" not in out
    stored = records.get("Bed Double 01")
    assert stored.guid == LIBRARY["Bed Double 01"] == out["guid"]
    assert (stored.kind, stored.tapir, stored.learned) == ("object", "1.7.2",
                                                          date.today().isoformat())
    assert stored.footprint == out["measured"] == {
        "xMin": [0.0, 0.0], "xMax": [1.0, 0.0], "yMin": [0.0, 0.0], "yMax": [1.0, 0.0]}
    assert stored.head is None and stored.one_sided is None
    assert out["next"] == ["head", "one_sided"]
    assert (out["part"], out["kind"]) == ("Bed Double 01", "object")


def test_object_samples_are_sent_at_the_sample_sizes_and_listed_left_to_right(records):
    fake = Fake(boxes_of_model=(-3, -2, 40, 9))
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    sent = fake.items("CreateObjects")
    assert out["samples"] == ["1.0 x 1.0 m", "2.0 x 1.5 m", "1.0 x 1.0 m mirrored"]
    assert [(i["dimensions"]["x"], i["dimensions"]["y"], i["dimensions"]["z"]) for i in sent] \
        == [(*SAMPLE_SIZES[0], SAMPLE_HEIGHT), (*SAMPLE_SIZES[1], SAMPLE_HEIGHT),
            (*SAMPLE_SIZES[0], SAMPLE_HEIGHT)]
    assert [i["reflected"] for i in sent] == [False, False, True]
    assert all(i["angle"] == 0 and i["libraryPartName"] == "Bed Double 01" for i in sent)
    xs = [i["coordinates"]["x"] for i in sent]
    assert xs[0] == 40 + CLEARANCE and xs == sorted(xs)
    assert all(i["coordinates"]["y"] == -2 for i in sent)


def left_of_insertion(a: float, b: float):
    """A part inserted at its foot-right corner: it draws to the left of the point."""
    return (a, b), (-a, 0.0, 0.0, b)


@pytest.mark.parametrize("draw", [corner_boxes(), left_of_insertion])
def test_samples_do_not_overlap_whatever_side_of_the_insertion_point_they_draw(records, draw):
    fake = Fake(boxes=draw)
    learn_library_part(fake.connection(), records, "Bed Double 01", "object",
                       [{"gs_cont_pen": 3}])
    boxes = sorted(fake.object_box(i) for i in fake.items("CreateObjects"))
    assert len(boxes) == 4
    assert all(left[2] < right[0] for left, right in zip(boxes, boxes[1:]))


def test_samples_go_past_the_model():
    assert sample_origin(Fake(boxes_of_model=(-3, -2, 40, 9)).connection()) == (45.0, -2.0)


def test_an_empty_model_takes_its_samples_at_the_origin():
    assert sample_origin(Fake().connection()) == (0.0, 0.0)


def test_thousands_of_elements_are_boxed_in_chunks():
    fake = Fake(model_elements=2500)
    sample_origin(fake.connection())
    assert fake.box_calls == 3
    sizes = [len(p["elements"]) for c, p in fake.core.calls if c == "API.Get2DBoundingBoxes"]
    assert sizes == [BOX_CHUNK, BOX_CHUNK, 500]


def test_a_failure_part_way_still_deletes_what_was_placed(records):
    fake = Fake(fail_create_at=2)
    out = learn_library_part(fake.connection(), records, "Window", "window")
    assert "error" in out and fake.remaining == [] and "Wall" in str(fake.deleted_types)
    assert "tf: reflected true, oSide false" not in out["error"]
    assert "ft: reflected false, oSide true" in out["error"]
    assert "deleted again" in out["error"] and records.get("Window") is None


def test_window_learning_says_the_tool_now_holds_the_part(records):
    out = learn_library_part(Fake().connection(), records, "Window", "window")
    assert any("Window tool now holds 'Window'" in w for w in out["warnings"])


def test_a_window_reply_says_what_missing_opening_lines_mean(records):
    out = learn_library_part(Fake().connection(), records, "Window", "window")
    assert "opening lines" in out["note"] and "ask the user" in out["note"]
    assert "note" not in learn_library_part(Fake().connection(), records, "Door", "door")


def test_openings_stand_in_one_temporary_wall_one_per_combination(records):
    fake = Fake(boxes_of_model=(0, 0, 10, 4))
    out = learn_library_part(fake.connection(), records, "Door", "door")
    [wall] = fake.items("CreateWalls")
    assert wall["begCoordinate"] == {"x": 15.0, "y": 0.0}
    assert wall["endCoordinate"] == {"x": 15.0 + 4 * SPACING, "y": 0.0}
    doors = fake.items("CreateDoors")
    assert [(d["reflected"], d["oSide"]) for d in doors] == [
        (c[0] == "t", c[1] == "t") for c in COMBOS]
    assert [d["centerOffset"] for d in doors] == [1.25, 3.75, 6.25, 8.75]
    assert all((d["width"], d["height"], d["sillHeight"], d["libraryPartName"])
               == (1.0, 2.1, 0.0, "Door") for d in doors)
    assert out["samples"] == ["ff: reflected false, oSide false", "tf: reflected true, oSide false",
                              "ft: reflected false, oSide true", "tt: reflected true, oSide true"]
    assert out["measured"] is None and out["next"] == ["combos", "leaves"]
    assert fake.remaining == [] and records.get("Door").kind == "door"
    assert any("Door tool now holds 'Door'" in w for w in out["warnings"])


def test_openings_are_sampled_with_ref_side_false(records):
    # The hinge and swing recorded hold for refSide false, so every sample sends it.
    fake = Fake()
    learn_library_part(fake.connection(), records, "Door", "door", [{"gs_leaf_1_open": 1}])
    doors = fake.items("CreateDoors")
    assert len(doors) == 5 and all(d["refSide"] is False for d in doors)


@pytest.mark.parametrize("flipped", [True, False])
def test_the_record_keeps_the_temporary_walls_flipped(records, flipped):
    out = learn_library_part(Fake(wall_flipped=flipped).connection(), records, "Door", "door")
    assert out["wall_flipped"] is flipped
    assert records.get("Door").wall_flipped is flipped


def test_a_wall_whose_flipped_cannot_be_read_is_a_warning(records):
    out = learn_library_part(Fake(wall_flipped=None).connection(), records, "Door", "door")
    assert "error" not in out and records.get("Door").wall_flipped is None
    assert any("flipped" in w and "could not be read" in w for w in out["warnings"])


def test_an_object_record_has_no_wall_flipped(records):
    out = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    assert "wall_flipped" not in out and records.get("Bed Double 01").wall_flipped is None


def test_windows_are_sampled_at_a_window_height_and_sill(records):
    fake = Fake()
    learn_library_part(fake.connection(), records, "Window", "window")
    assert {(w["width"], w["height"], w["sillHeight"]) for w in fake.items("CreateWindows")} \
        == {(1.0, 1.2, 0.9)}


def test_door_parameters_that_cannot_be_read_are_a_warning_not_an_error(records):
    out = learn_library_part(Fake().connection(), records, "Door", "door")
    assert "error" not in out and out["parameters"] == []
    assert any("parameters could not be read, probably because Tapir 1.7.2 places doors and "
               "windows by part name" in w for w in out["warnings"])


def test_the_first_samples_parameters_come_back_by_name_type_and_value(records):
    out = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    assert out["parameters"][:2] == [{"name": "A", "type": "Length", "value": 1.0},
                                     {"name": "B", "type": "Length", "value": 1.0}]


def test_the_footprint_is_fitted_against_the_size_the_part_took(records):
    # Bed Double 01 draws B 1.2 when sent 1.0, and its GDL B reads 1.2.
    out = learn_library_part(Fake(boxes=corner_boxes(min_b=1.2)).connection(), records,
                             "Bed Double 01", "object")
    assert out["measured"]["yMax"] == pytest.approx([1.0, 0.0])
    assert any("sent at A 1, B 1 and the part took A 1, B 1.2" in w for w in out["warnings"])


def test_sizes_that_cannot_be_read_fall_back_to_the_sizes_sent(records):
    out = learn_library_part(Fake(gdl_fails=True).connection(), records, "Bed Double 01",
                             "object")
    assert out["measured"]["yMax"] == pytest.approx([1.0, 0.0])
    assert any("A and B of sample '1.0 x 1.0 m' could not be read" in w for w in out["warnings"])
    assert out["parameters"] == []
    # Without the read-back, a clamped B is a box that grows slower than the B sent
    # (1.0 drew 1.2, 1.5 drew 1.5): no footprint, rather than 0.6 B + 0.6.
    clamped = learn_library_part(Fake(boxes=corner_boxes(min_b=1.2), gdl_fails=True)
                                 .connection(), records, "Bed Double 01", "object")
    assert clamped["measured"] is None and "depth" in clamped["note"]


def chair_boxes(a: float, b: float):
    """Chair 01 live (22.10.2026): its GDL forces A = B (B wins), while its plan width
    follows the A sent and its depth max(A, B) sent, centred on the insertion point."""
    depth = max(a, b)
    return (b, b), (-a / 2, -depth / 2, a / 2, depth / 2)


def test_a_part_whose_plan_size_does_not_follow_its_a_and_b_gets_no_footprint(records):
    fake = Fake(boxes=chair_boxes)
    out = learn_library_part(fake.connection(), records, "Chair 01", "object")
    assert "error" not in out and fake.remaining == []
    # x follows the A sent (1 to 2, the box 1 to 2 wide), not the A read back (1 to 1.5).
    assert any("plan width follows the A sent" in w for w in out["warnings"])
    # y grows 1 to 2 while B goes 1 to 1.5, sent and read back alike.
    assert out["measured"] is None and records.get("Chair 01").footprint is None
    assert out["next"] == ["footprint", "head", "one_sided"]
    note = out["note"]
    assert "plan size does not follow its own A and B" in note
    assert "plan depth" in note and "plan width" not in note
    assert "coordinates" in note


def test_a_part_that_mirrors_some_other_way_is_warned_about(records):
    plain = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    assert not any("mirror" in w for w in plain["warnings"])
    odd = learn_library_part(Fake(mirror_axis="y").connection(), records, "Bed Double 01",
                             "object")
    assert any("does not mirror about its own y axis" in w for w in odd["warnings"])


def test_the_plan_is_zoomed_on_the_samples_and_the_reply_says_it_stays_so(records):
    fake = Fake()
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    [fit] = fake.sent["FitInWindow"]
    assert [e["elementId"]["guid"] for e in fit["elements"]] == fake.created_guids
    assert any("stays zoomed" in w for w in out["warnings"])


@pytest.mark.parametrize("kind, name", [("object", "Bed Double 01"), ("door", "Door")])
def test_a_rotated_plan_view_is_refused_and_every_sample_deleted(records, kind, name):
    # The agent reads head, hinge and swing on screen: in a rotated view they would be
    # recorded turned.
    fake = Fake(rotation=math.pi / 2)
    out = learn_library_part(fake.connection(), records, name, kind)
    assert "rotated by 90 degrees" in out["error"]
    assert "orientation" in out["error"] and "learn" in out["error"]
    assert "deleted again" in out["error"]
    assert fake.created_guids and fake.remaining == []
    assert records.get(name) is None and "png" not in out


def test_a_plan_turned_a_full_circle_is_not_rotated(records):
    out = learn_library_part(Fake(rotation=2 * math.pi).connection(), records,
                             "Bed Double 01", "object")
    assert "error" not in out


def test_a_capture_that_does_not_say_its_rotation_is_warned_about(records):
    out = learn_library_part(Fake(rotation=None).connection(), records, "Bed Double 01",
                             "object")
    assert "error" not in out
    assert any("did not say whether the plan view is rotated" in w for w in out["warnings"])
    plain = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    assert not any("rotated" in w for w in plain["warnings"])


def test_refuses_outside_the_floor_plan(records):
    fake = Fake(window="Layout")
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    assert "floor plan" in out["error"] and "Layout" in out["error"]
    assert fake.created_guids == [] and records.get("Bed Double 01") is None


def test_refuses_an_unknown_part_with_the_closest_names(records):
    fake = Fake()
    out = learn_library_part(fake.connection(), records, "Bed Dubble 01", "object")
    assert "no loaded library part named 'Bed Dubble 01'; closest: Bed Double 01" in out["error"]
    assert fake.created_guids == []


def test_refuses_a_name_the_library_spells_another_way(records):
    fake = Fake()
    out = learn_library_part(fake.connection(), records, "window", "window")
    assert "spells 'window' as 'Window'" in out["error"] and fake.created_guids == []


def test_refuses_doors_on_tapir_older_than_1_7_2(records):
    fake = Fake(tapir_version="1.7.1")
    out = learn_library_part(fake.connection(), records, "Door", "door")
    assert "1.7.2" in out["error"] and "1.7.1" in out["error"]
    assert fake.created_guids == []
    assert "error" not in learn_library_part(fake.connection(), records, "Bed Double 01",
                                             "object")


@pytest.mark.parametrize("kind, variants", [("furniture", None), ("object", [{}]),
                                            ("object", ["gs_cont_pen"]),
                                            ("door", [{"": 1}])])
def test_refuses_a_bad_kind_or_variant_before_placing(records, kind, variants):
    fake = Fake()
    out = learn_library_part(fake.connection(), records, "Bed Double 01", kind, variants)
    assert "error" in out and fake.created_guids == []


def test_relearning_the_same_guid_keeps_the_agents_fields(records):
    records.save(PartRecord(name="Bed Double 01", guid=LIBRARY["Bed Double 01"].lower(),
                            kind="object", learned="2026-01-05", tapir="1.7.1",
                            footprint={"xMin": [0, 9], "xMax": [0, 9], "yMin": [0, 9],
                                       "yMax": [0, 9]},
                            head="+y", one_sided=True, height=0.55))
    out = learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    kept = records.get("Bed Double 01")
    assert (kept.head, kept.one_sided, kept.height) == ("+y", True, 0.55)
    assert kept.footprint["xMax"] == [1.0, 0.0]
    assert (kept.learned, kept.tapir) == (date.today().isoformat(), "1.7.2")
    assert out["next"] == []


def test_relearning_a_part_with_another_guid_starts_a_fresh_record(records):
    records.save(PartRecord(name="Bed Double 01", guid="00000000-0000-4000-8000-000000000001",
                            kind="object", learned="2026-01-05", head="+y", one_sided=True))
    learn_library_part(Fake().connection(), records, "Bed Double 01", "object")
    fresh = records.get("Bed Double 01")
    assert fresh.guid == LIBRARY["Bed Double 01"] and fresh.head is None


def test_a_refused_variant_is_reported_not_fatal(records):
    fake = Fake(refuse_parameters={"no_such"})
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object",
                             [{"gs_cont_pen": 3}, {"no_such": 1}])
    assert "error" not in out and len(out["samples"]) == 5
    assert out["samples"][3:] == ['1.0 x 1.0 m, variant 1: gs_cont_pen = 3',
                                  '1.0 x 1.0 m, variant 2: no_such = 1']
    [sent] = fake.sent["SetGDLParametersOfElements"]
    assert [e["gdlParameters"] for e in sent["elementsWithGDLParameters"]] == [
        [{"name": "gs_cont_pen", "value": 3}], [{"name": "no_such", "value": 1}]]
    refused = [w for w in out["warnings"] if "variant 2" in w]
    assert refused and "Invalid parameter name: no_such" in refused[0]
    assert not any("variant 1" in w for w in out["warnings"])
    assert fake.remaining == []


def test_door_variants_stand_after_the_four_combinations_with_combo_ff(records):
    fake = Fake()
    out = learn_library_part(fake.connection(), records, "Door", "door", [{"gs_leaf": 2}])
    doors = fake.items("CreateDoors")
    assert len(doors) == 5 and (doors[4]["reflected"], doors[4]["oSide"]) == (False, False)
    assert doors[4]["centerOffset"] == 11.25
    assert fake.items("CreateWalls")[0]["endCoordinate"]["x"] == 5 * SPACING
    assert out["samples"][4] == "ff, variant 1: gs_leaf = 2"


def test_a_sample_that_will_not_go_is_named_in_an_error(records):
    fake = Fake(keep={1})
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    survivor = fake.created_guids[1]
    assert survivor in out["error"] and out["left_in_model"] == [survivor]
    assert "The element cannot be deleted" in out["error"]
    assert fake.remaining == [survivor] and records.get("Bed Double 01") is None


def test_a_failed_capture_still_deletes_everything(records):
    fake = Fake(capture=False)
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    assert "could not be captured" in out["error"] and fake.remaining == []
    assert records.get("Bed Double 01") is None


def test_samples_go_on_the_story_the_floor_plan_shows(records):
    # Story 2 is on screen, at level 6.0. Without floorIndex, Tapir would put z 0
    # on the ground story, where the capture of story 2 does not see it.
    fake = Fake(act_story=2)
    learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    assert {(i["floorIndex"], i["coordinates"]["z"]) for i in fake.items("CreateObjects")} \
        == {(2, 6.0)}
    learn_library_part(fake.connection(), records, "Door", "door")
    [wall] = fake.items("CreateWalls")
    assert wall["floorIndex"] == 2 and "zCoordinate" not in wall


def test_refuses_when_the_current_story_cannot_be_read(records):
    fake = Fake(stories=[])
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    assert "story" in out["error"] and fake.created_guids == []


@pytest.mark.parametrize("name, kind", [("Bed Double 01", "object"), ("Door", "door")])
def test_a_sample_no_reply_named_is_found_and_deleted(records, name, kind):
    fake = Fake(create_raises=True)
    out = learn_library_part(fake.connection(), records, name, kind)
    stray = fake.created_guids[-1]          # the door after its wall, or the one object
    assert fake.remaining == [] and fake.deleted[0] == stray    # before the wall, if any
    assert sorted(fake.deleted) == sorted(fake.created_guids)
    assert "The command stopped" in out["error"] and "deleted again" in out["error"]
    assert "left_in_model" not in out


def test_a_stray_sample_that_will_not_go_is_named(records):
    fake = Fake(create_raises=True, keep={0})
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    [stray] = fake.remaining
    assert out["left_in_model"] == [stray] and stray in out["error"]


@pytest.mark.parametrize("element_type, box", [
    ("Object", (2, 1, 3, 2)),              # the sampled type, inside the old extent
    ("Zone", (20, -2, 24, 2)),             # past the extent, a type learning never places
    ("Wall", (20, -2, 24, -1.7)),          # a wall, but an object is being learned
])
def test_elements_the_user_adds_while_learning_are_left_alone(records, element_type, box):
    fake = Fake(boxes_of_model=(0, 0, 10, 4), appear=[(element_type, box)])
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    assert "error" not in out and "user-0" in fake.model
    assert "user-0" not in fake.deleted and not any("user-0" in w for w in out["warnings"])
    assert fake.remaining == [] and records.get("Bed Double 01") is not None


FOOTPRINT = {"xMin": [0.0, 0.0], "xMax": [1.0, 0.0], "yMin": [0.0, 0.0], "yMax": [1.0, 0.0]}


def learned(tmp_path, name: str, kind: str) -> PartRecords:
    """A store holding what learning measured for the part and nothing the agent judged."""
    records = PartRecords(tmp_path / "r.json")
    records.save(PartRecord(name=name, guid=LIBRARY[name], kind=kind, learned="2026-10-22",
                            tapir="1.7.2",
                            footprint=FOOTPRINT if kind in ("object", "lamp") else None))
    return records


def test_recording_the_agents_fields_completes_the_record(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Bed Double 01", head="+y", one_sided=False, height=0.55)
    assert "error" not in out
    assert out["missing"] == [] and records.get("Bed Double 01").head == "+y"
    assert out["record"] == records.get("Bed Double 01").to_json()
    assert (out["record"]["one_sided"], out["record"]["height"]) == (False, 0.55)
    assert out["record"]["footprint"] == FOOTPRINT       # what learning measured stays


def test_what_is_still_missing_is_named(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Bed Double 01", head="-x")
    assert out["missing"] == ["one_sided"]


def test_an_opening_takes_combos_leaves_and_leaf_states(tmp_path):
    records = learned(tmp_path, "Window", "window")
    combos = {"ff": ("begin", "left"), "tf": ["end", "left"], "ft": ["begin", "right"],
              "tt": ["end", "right"]}
    states = {"left sash open": {"gs_leaf_1_open": 1, "gs_leaf_2_open": 0}}
    out = record_library_part(records, "Window", combos=combos, leaves=2, leaf_states=states)
    assert out["missing"] == []
    stored = records.get("Window")
    assert stored.combos["ff"] == ["begin", "left"] and type(stored.combos["ff"]) is list
    assert all(type(value) is list for value in out["record"]["combos"].values())
    assert (stored.leaves, stored.leaf_states) == (2, states)


def test_combos_and_leaf_states_recorded_later_add_to_the_earlier_ones(tmp_path):
    records = learned(tmp_path, "Door", "door")
    record_library_part(records, "Door", combos={"ff": ["begin", "left"]},
                        leaf_states={"open": {"gs_open": 1}})
    out = record_library_part(records, "Door", combos={"tf": ["end", "left"],
                                                       "ff": ["begin", "right"]},
                              leaf_states={"shut": {"gs_open": 0}})
    assert out["record"]["combos"] == {"ff": ["begin", "right"], "tf": ["end", "left"]}
    assert out["record"]["leaf_states"] == {"open": {"gs_open": 1}, "shut": {"gs_open": 0}}


def test_recording_again_replaces_a_single_value(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    record_library_part(records, "Bed Double 01", head="+y", one_sided=True)
    out = record_library_part(records, "Bed Double 01", head="-x")
    assert out["record"]["head"] == "-x" and out["record"]["one_sided"] is True


def test_recording_nothing_reads_the_record_back(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Bed Double 01")
    assert out["record"]["name"] == "Bed Double 01" and out["missing"] == ["head", "one_sided"]


def test_recording_a_part_never_learned_is_refused(tmp_path):
    records = PartRecords(tmp_path / "r.json")
    out = record_library_part(records, "Bed Double 01", head="+y")
    assert "learn_library_part" in out["error"] and "Bed Double 01" in out["error"]
    assert "record" not in out and not (tmp_path / "r.json").exists()


@pytest.mark.parametrize("fields", [
    {"head": "up"}, {"head": "+z"}, {"head": 1},
    {"combos": {"xx": ["begin", "left"]}}, {"combos": {"ff": ["top", "left"]}},
    {"combos": {"ff": ["begin", "up"]}}, {"combos": {"ff": ["left", "begin"]}},
    {"combos": {"ff": ["begin"]}}, {"combos": {"ff": "begin left"}},
    {"combos": {"ff": ["begin", "left", "x"]}}, {"combos": ["ff"]}, {"combos": {}},
    {"leaves": 0}, {"leaves": -1}, {"leaves": 1.5}, {"leaves": True}, {"leaves": "2"},
    {"leaf_states": {"left": 1}}, {"leaf_states": {"left": {1: 2}}}, {"leaf_states": []},
    {"leaf_states": {"left": {}}}, {"leaf_states": {"": {"gs_open": 1}}},
    {"height": 0}, {"height": -0.5}, {"height": "tall"}, {"height": True},
    {"one_sided": "yes"}, {"one_sided": 1},
    {"head": "+y", "leaves": 0},                  # one good value beside a bad one
])
def test_bad_values_are_refused_and_nothing_is_saved(tmp_path, fields):
    records = learned(tmp_path, "Bed Double 01", "object")
    before = (tmp_path / "r.json").read_bytes()
    out = record_library_part(records, "Bed Double 01", **fields)
    assert "error" in out and "record" not in out
    assert (tmp_path / "r.json").read_bytes() == before


def test_the_error_names_the_value_and_what_is_allowed(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    assert "+x" in record_library_part(records, "Bed Double 01", head="up")["error"]
    assert "ff" in record_library_part(records, "Bed Double 01",
                                       combos={"xx": ["begin", "left"]})["error"]
    assert "begin" in record_library_part(records, "Bed Double 01",
                                          combos={"ff": ["top", "left"]})["error"]


def test_forget_removes_the_record(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Bed Double 01", forget=True)
    assert out == {"forgotten": True, "name": "Bed Double 01"}
    assert records.get("Bed Double 01") is None


def test_forgetting_a_part_with_no_record_changes_nothing(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Chair 01", forget=True)
    assert out["forgotten"] is False and "error" not in out
    assert records.get("Bed Double 01") is not None


def test_forget_does_not_come_with_other_fields(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    out = record_library_part(records, "Bed Double 01", head="+y", forget=True)
    assert "error" in out and records.get("Bed Double 01") is not None
    assert records.get("Bed Double 01").head is None


def test_an_unreadable_file_is_an_error_and_is_left_as_it_is(tmp_path):
    (tmp_path / "r.json").write_text("{not json", encoding="utf-8")
    records = PartRecords(tmp_path / "r.json")
    for out in (record_library_part(records, "Bed Double 01", head="+y"),
                record_library_part(records, "Bed Double 01", forget=True),
                list_library_part_records(records)):
        assert str(tmp_path / "r.json") in out["error"]
    assert (tmp_path / "r.json").read_text(encoding="utf-8") == "{not json"


def test_list_names_the_file_and_filters_by_name(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    records.save(PartRecord(name="Window", guid=LIBRARY["Window"], kind="window",
                            learned="2026-10-22"))
    everything = list_library_part_records(records)
    assert everything["path"] == str(tmp_path / "r.json")
    assert {r["name"] for r in everything["records"]} == {"Bed Double 01", "Window"}
    chosen = list_library_part_records(records, ["Window", "Chair 01"])
    assert [r["name"] for r in chosen["records"]] == ["Window"]
    assert chosen["not_recorded"] == ["Chair 01"] and chosen["path"] == everything["path"]
    assert "not_recorded" not in everything


def test_list_says_what_each_record_still_needs(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")
    [only] = list_library_part_records(records)["records"]
    assert only["missing"] == ["head", "one_sided"] and only["footprint"] == FOOTPRINT


def test_listing_an_empty_store_gives_no_records_and_the_path(tmp_path):
    out = list_library_part_records(PartRecords(tmp_path / "none.json"))
    assert out == {"records": [], "path": str(tmp_path / "none.json")}
    assert not (tmp_path / "none.json").exists()


def test_learning_is_gated_on_every_command_it_sends():
    assert TOOL_COMMANDS["learn_library_part"] == (
        "GetAvailableLibraryParts", "GetCurrentWindowType", "GetStories", "CreateObjects",
        "CreateLamps", "CreateWalls", "CreateWindows", "CreateDoors",
        "GetGDLParametersOfElements", "SetGDLParametersOfElements", "DeleteElements",
        "FitInWindow", "SaveCurrentWindowAsImage",
        # the model's elements before and after, and the check that the samples are gone
        "GetAllElements", "GetDetailsOfElements")


@pytest.mark.parametrize("kind, name", [("object", "Bed Double 01"), ("lamp", "Chair 01"),
                                        ("door", "Door"), ("window", "Window")])
def test_every_tapir_command_learning_sends_is_in_its_gate(tmp_path, kind, name):
    fake = Fake()
    connection = fake.connection()
    out = learn_library_part(connection, PartRecords(tmp_path / "r.json"), name, kind)
    assert "error" not in out
    tapir = {command for command, _ in fake.core.calls if command in fake.core.tapir_responses}
    # GetAddOnVersion only names the version; a Tapir that cannot answer it is let through.
    assert tapir - {"GetAddOnVersion"} <= set(TOOL_COMMANDS["learn_library_part"])
