"""learn_library_part: samples placed past the model, measured, captured and deleted again."""
from __future__ import annotations

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
    sample_origin,
)
from archicad_mcp.core.part_records import COMBOS, PartRecord, PartRecords
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
    and then fails the whole command, so no reply names it.
    """

    def __init__(self, boxes=None, boxes_of_model=None, model_elements=0, window="FloorPlan",
                 tapir_version="1.7.2", library=None, fail_create_at=None, gdl_fails=False,
                 refuse_parameters=(), keep=(), capture=True, create_raises=False,
                 mirror_axis="x"):
        self.draw = boxes or corner_boxes()
        self.mirror_axis = mirror_axis       # the local axis `reflected` negates
        self.model: dict[str, tuple] = {}
        if boxes_of_model is not None:
            self.model["model-0"] = tuple(float(v) for v in boxes_of_model)
        for i in range(model_elements):
            self.model[f"model-{i}"] = (float(i), 0.0, i + 1.0, 1.0)
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
        return None

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
        return {"detailsOfElements": [
            {"type": self.elements[e["elementId"]["guid"]]["type"]}
            if e["elementId"]["guid"] in self.elements
            else {"error": {"code": -2130313112, "message": "not found"}}
            for e in p["elements"]]}

    def _save(self, p):
        if self.capture:
            Image.new("RGB", (640, 360), "white").save(p["imageFilePath"], "PNG")
        return {"imageFilePath": p["imageFilePath"]}

    def _fit(self, p):
        self.sent.setdefault("FitInWindow", []).append(p)
        return {"success": True}

    def connection(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.Get2DBoundingBoxes"] = self._boxes
        tapir = {
            "GetCurrentWindowType": lambda p: {"currentWindowType": self.window},
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


def test_windows_are_sampled_at_a_window_height_and_sill(records):
    fake = Fake()
    learn_library_part(fake.connection(), records, "Window", "window")
    assert {(w["width"], w["height"], w["sillHeight"]) for w in fake.items("CreateWindows")} \
        == {(1.0, 1.2, 0.9)}


def test_door_parameters_that_cannot_be_read_are_a_warning_not_an_error(records):
    out = learn_library_part(Fake().connection(), records, "Door", "door")
    assert "error" not in out and out["parameters"] == []
    assert any("parameters could not be read: Tapir 1.7.2 refuses them for doors and windows "
               "placed through the API" in w for w in out["warnings"])


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
    out = learn_library_part(Fake(boxes=corner_boxes(min_b=1.2), gdl_fails=True).connection(),
                             records, "Bed Double 01", "object")
    assert out["measured"]["yMax"] == pytest.approx([0.6, 0.6])    # 1.0 drew 1.2, 1.5 drew 1.5
    assert any("A and B of sample '1.0 x 1.0 m' could not be read" in w for w in out["warnings"])
    assert out["parameters"] == []


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


def test_an_element_no_reply_named_is_reported_as_left_in_the_model(records):
    fake = Fake(create_raises=True)
    out = learn_library_part(fake.connection(), records, "Bed Double 01", "object")
    [stray] = fake.remaining
    assert stray in out["error"] and out["left_in_model"] == [stray]
    assert "The command stopped" in out["error"]
