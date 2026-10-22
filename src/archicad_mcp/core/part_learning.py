"""learn_library_part: a few samples of a library part, measured, shown and removed.

No API says where a part's insertion point sits on its footprint, which way its
head points, or what `reflected` and `oSide` do to a door's hinge and swing. So
the part is placed a few times in a row just past the model, on the story the
floor plan shows, its footprint is measured from the floor plan boxes, the row is
captured in one floor plan image for the agent to judge, and every sample is
deleted again in the same call, also when something fails part way. The
measurement goes into the part's record; what only a look tells, the agent
records afterwards (record_library_part).

The image is a capture of the floor plan zoomed on the samples, not
GetElementPreviewImage: live 22.10.2026 (AC 29, Tapir 1.7.2) a 2D preview draws
at a fixed 100 px per metre around the insertion point, so a 2 m bed is cut off,
and it shows neither a door's hinge nor its swing. Tapir cannot set the zoom
back, so the reply says the plan stays zoomed there.

Objects and lamps are sampled at two explicit sizes, because on Tapir 1.7.2 a
part sent without dimensions takes the Object tool's size. Each axis of the
footprint is fitted against whichever of the A and B read back from the GDL and
the A and B sent its plan box follows (part_placement.fit_axis): Bed Double 01
clamps B to at least 1.2, so a sample sent at 1.0 x 1.0 is drawn 1.0 x 1.2 and
its GDL reads B 1.2; Chair 01's GDL forces A = B, while its plan width follows
the A sent and its depth neither. A part whose plan size follows neither on an
axis gets no footprint, and is placed by coordinates.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from datetime import date

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.actions import fit_in_window
from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.create import FLOOR_PLAN, closest_names
from archicad_mcp.core.editability import active_window, present
from archicad_mcp.core.element_data import error_fields
from archicad_mcp.core.mutate import _refused_deletions, _survivor_reason
from archicad_mcp.core.part_placement import Sample, fit_axis
from archicad_mcp.core.part_records import (
    COMBOS,
    HEADS,
    KINDS,
    PartRecord,
    PartRecords,
    RecordsFileError,
    identity_problem,
)
from archicad_mcp.core.window_image import capture_window
from archicad_mcp.extract import _fetch_types, element_payload, get_all_element_ids
from archicad_mcp.tapir_requirements import installed_version

# Objects and lamps: the sizes (A, B) sampled, so each edge can be fitted as a
# function of the size; the mirrored and variant samples take the first one.
SAMPLE_SIZES = ((1.0, 1.0), (2.0, 1.5))
SAMPLE_HEIGHT = 1.0

# Metres from the model's right edge to the first sample.
CLEARANCE = 5.0

# Metres between samples in their row (see _object_samples), and between the
# centres of the openings along the temporary wall.
SPACING = 2.5

# Elements per Get2DBoundingBoxes request: a model of tens of thousands of
# elements is boxed in a few calls instead of one request that wide.
BOX_CHUNK = 1000

# Door and window samples: width, and (height, sill) per kind.
OPENING_WIDTH = 1.0
_OPENING_SIZES = {"window": (1.2, 0.9), "door": (2.1, 0.0)}

# The temporary wall is given a height, so a Wall tool set low cannot refuse
# a 2.1 m door. The rest comes from the Wall tool.
WALL_HEIGHT = 3.0

# libraryPartName on CreateDoors and CreateWindows came in Tapir 1.7.2. It is a
# field, not a command, so only the version can tell.
_OPENINGS_BY_NAME = (1, 7, 2)

_CREATE = {"object": ("CreateObjects", "objectsData"), "lamp": ("CreateLamps", "lampsData"),
           "door": ("CreateDoors", "doorsData"), "window": ("CreateWindows", "windowsData")}

# Sizes closer than this count as the size sent.
_SAME = 1e-6

# How far (m) the mirrored sample's box may stray from the mirror image of the
# first sample's before the reply says the part does not mirror about local y.
_MIRROR_TOLERANCE = 1e-3

_ZOOMED = ("The floor plan stays zoomed where the samples stood: Tapir has no command "
           "to set the zoom back.")
_NOT_ZOOMED = ("The floor plan could not be zoomed to the samples, so the capture shows "
               "the window as it was and may not show them.")
_WINDOW_NOTE = ("If the capture shows the windows without opening lines, the model's view "
                "settings hide window opening lines (Model View Options), and the samples "
                "cannot show hinge or swing: ask the user to show them, then learn the "
                "window again, before recording hinge and swing.")
_UNCHECKED = "Archicad did not answer the check afterwards, so it may still be there"

# The element types a learning call places, per kind: an element of one of these
# that appears past the old model extent while learning is a sample whose GUID
# no reply carried (see _strays).
_SAMPLED_TYPES = {"object": ("Object",), "lamp": ("Lamp",), "door": ("Door", "Wall"),
                  "window": ("Window", "Wall")}


# Per footprint axis: what its extent is called, and the size it should follow.
_AXIS_WORDS = {"x": ("width", "A"), "y": ("depth", "B")}

# The words a recorded combination is made of: where the hinge sits along the wall
# from its begin point to its end, and which side of the wall the door swings to.
_HINGES = ("begin", "end")
_SWINGS = ("left", "right")


class _Failed(Exception):
    """Learning cannot go on; the message is the reply's error."""


class _Refused(ValueError):
    """A value record_library_part was given cannot be recorded; the message says why."""


@dataclass
class _Placed:
    label: str
    item: dict
    a: float = 0.0
    b: float = 0.0
    variant: dict | None = None
    guid: str | None = None


def sample_origin(conn: ArchicadConnection) -> tuple[float, float]:
    """Where the row of samples starts: CLEARANCE past the model's right edge, level
    with its lowest point; (0, 0) in an empty model."""
    return _origin(_boxes(conn, get_all_element_ids(conn)))


def learn_library_part(conn: ArchicadConnection, records: PartRecords, name: str, kind: str,
                       variants: list[dict] | None = None) -> dict:
    variants = list(variants or [])
    problem = _bad_arguments(kind, variants)
    if problem is not None:
        return {"error": problem}
    window = active_window(conn)
    if window != FLOOR_PLAN:
        return {"error": f"Learning places its samples on the floor plan, and the active "
                         f"window is {window or 'unknown'}: switch to the floor plan and "
                         "retry. Nothing was placed."}
    tapir = installed_version(conn)
    if kind in ("door", "window") and _older(tapir, _OPENINGS_BY_NAME):
        return {"error": f"Learning a {kind} needs Tapir 1.7.2 or newer, which places doors "
                         f"and windows by part name; this Archicad has Tapir {tapir}. "
                         "Nothing was placed. install_tapir updates it."}
    part_guid, problem, notes = _loaded_part(conn, name)
    if problem is not None:
        return {"error": problem}
    try:
        earlier = records.get(name)
    except RecordsFileError as error:
        return {"error": f"{error} Nothing was placed."}
    try:
        story = _current_story(conn)
        before = get_all_element_ids(conn)
        origin = _origin(_boxes(conn, before))
    except (APIErrorBase, ArchicadUnavailableError) as exc:
        return {"error": f"The model's extent or the current story could not be read "
                         f"({error_fields(exc)['message']}), so no spot for the samples is "
                         "known. Nothing was placed."}
    if story is None:
        return {"error": "Archicad did not say which story the floor plan shows, or its "
                         "level, so the samples could not be put where the capture sees "
                         "them. Nothing was placed."}

    learning = _Learning(conn, name, kind, origin, story, variants)
    learning.warnings.extend(notes)
    left: dict[str, str] = {}
    try:
        outcome = learning.run()
    except _Failed as failure:
        outcome = {"error": str(failure)}
    except (APIErrorBase, ArchicadUnavailableError) as exc:
        outcome = {"error": f"Learning stopped: {error_fields(exc)['message']}."}
    except Exception as exc:    # whatever stopped it, the reply must say what is left
        outcome = {"error": f"Learning stopped on {type(exc).__name__}: {exc}."}
    finally:
        strays = _strays(conn, before, learning.placed(), kind, origin[0])
        left = _remove(conn, learning.to_delete(strays))

    side_effects = {"warnings": learning.warnings} if learning.warnings else {}
    if left:
        listed = "; ".join(f"{element} ({why})" for element, why in left.items())
        lead = f"{outcome['error']} " if "error" in outcome else ""
        return {"error": f"{lead}Elements placed for learning are still in the model: "
                         f"{listed}. Delete them (delete_elements) before going on; "
                         "nothing was recorded.",
                "left_in_model": list(left), **side_effects}
    if "error" in outcome:
        return {"error": f"{outcome['error']} Every element placed for learning was deleted "
                         "again; nothing was recorded.", **side_effects}

    record = _record(earlier, name, part_guid, kind, tapir, outcome["measured"])
    try:
        records.save(record)
    except RecordsFileError as error:
        return {"error": f"{error} The samples were measured and deleted again; nothing "
                         "was recorded.", **side_effects}
    result = {"part": name, "kind": kind, "guid": part_guid, **outcome,
              "warnings": learning.warnings, "next": record.missing()}
    if kind == "window":
        result["note"] = _WINDOW_NOTE
    elif learning.note is not None:
        result["note"] = learning.note
    return result


def record_library_part(records: PartRecords, name: str, head: str | None = None,
                        one_sided: bool | None = None, height: float | None = None,
                        combos: dict | None = None, leaves: int | None = None,
                        leaf_states: dict | None = None, forget: bool = False) -> dict:
    """Put what the agent judged from the learning image into the part's record.

    A part never learned has no record to put it in, and a refused value saves
    nothing, the valid ones beside it included. `combos` and `leaf_states` add to
    what is recorded (a key given again replaces its value); every other field
    replaces its value. `forget` removes the record instead and takes no fields.
    """
    given = {"head": head, "one_sided": one_sided, "height": height, "combos": combos,
             "leaves": leaves, "leaf_states": leaf_states}
    try:
        if forget:
            return _forgotten(records, name, [key for key, value in given.items()
                                              if value is not None])
        record = records.get(name)
        if record is None:
            return {"error": f"No record is kept for '{name}': call learn_library_part for "
                             "it first (it places samples, measures them and shows them in "
                             "an image), then record what the image shows. Nothing was "
                             "recorded."}
        try:
            changes = _checked(record, given)
        except _Refused as refusal:
            return {"error": f"{refusal} Nothing was recorded."}
        if changes:
            record = replace(record, **changes)
            records.save(record)
        return {"record": record.to_json(), "missing": record.missing()}
    except RecordsFileError as error:
        return {"error": str(error)}


def list_library_part_records(records: PartRecords, names: list[str] | None = None) -> dict:
    """The kept records (those named in `names`, when given and not empty), each with
    what is still missing from it, and the file they live in."""
    try:
        kept = {record.name: record for record in records.all()}
    except RecordsFileError as error:
        return {"error": str(error)}
    if not names:
        chosen = list(kept.values())
        absent: list[str] = []
    else:
        wanted = list(dict.fromkeys(names))
        chosen = [kept[n] for n in wanted if n in kept]
        absent = [n for n in wanted if n not in kept]
    result: dict = {"records": [{**record.to_json(), "missing": record.missing()}
                                for record in chosen],
                    "path": str(records.path)}
    if absent:
        result["not_recorded"] = absent
    return result


def _forgotten(records: PartRecords, name: str, given: list[str]) -> dict:
    if given:
        return {"error": f"forget removes the whole record and takes no other field, but "
                         f"{', '.join(given)} came with it. Nothing was changed."}
    removed = records.forget(name)
    result = {"forgotten": removed, "name": name}
    if not removed:
        result["note"] = f"No record was kept for '{name}'."
    return result


def _checked(record: PartRecord, given: dict) -> dict:
    """The record's changes for the fields given, or _Refused on the first bad value."""
    changes: dict = {}
    if given["head"] is not None:
        if given["head"] not in HEADS:
            raise _Refused(f"head must be one of {', '.join(HEADS)} (the direction the head "
                           f"points in the part's own axes), not {given['head']!r}.")
        changes["head"] = given["head"]
    if given["one_sided"] is not None:
        if not isinstance(given["one_sided"], bool):
            raise _Refused(f"one_sided must be true or false, not {given['one_sided']!r}.")
        changes["one_sided"] = given["one_sided"]
    if given["height"] is not None:
        height = given["height"]
        if isinstance(height, bool) or not isinstance(height, (int, float)) \
                or not math.isfinite(height) or height <= 0:
            raise _Refused(f"height must be a positive number of metres, not {height!r}.")
        changes["height"] = float(height)
    if given["leaves"] is not None:
        leaves = given["leaves"]
        if isinstance(leaves, bool) or not isinstance(leaves, int) or leaves < 1:
            raise _Refused(f"leaves must be a whole number of at least 1, not {leaves!r}.")
        changes["leaves"] = leaves
    if given["combos"] is not None:
        changes["combos"] = {**(record.combos or {}), **_checked_combos(given["combos"])}
    if given["leaf_states"] is not None:
        changes["leaf_states"] = {**(record.leaf_states or {}),
                                  **_checked_states(given["leaf_states"])}
    return changes


def _checked_combos(combos: object) -> dict[str, list[str]]:
    if not isinstance(combos, dict) or not combos:
        raise _Refused("combos must be an object of flag pairs (" + ", ".join(COMBOS)
                       + ") to [hinge, swing], such as {\"ff\": [\"begin\", \"right\"]}.")
    out: dict[str, list[str]] = {}
    for combo, pair in combos.items():
        if combo not in COMBOS:
            raise _Refused(f"A combo is one of {', '.join(COMBOS)} (reflected then oSide, "
                           f"t for true, f for false), not {combo!r}.")
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise _Refused(f"Combo {combo} must be [hinge, swing], such as [\"begin\", "
                           f"\"right\"], not {pair!r}.")
        hinge, swing = pair
        if hinge not in _HINGES:
            raise _Refused(f"Combo {combo}: the hinge is {' or '.join(_HINGES)}, "
                           f"not {hinge!r}.")
        if swing not in _SWINGS:
            raise _Refused(f"Combo {combo}: the swing is {' or '.join(_SWINGS)}, "
                           f"not {swing!r}.")
        out[combo] = [hinge, swing]
    return out


def _checked_states(states: object) -> dict[str, dict]:
    if not isinstance(states, dict) or not states:
        raise _Refused("leaf_states must be an object of state names to GDL parameter "
                       "values, such as {\"left sash open\": {\"gs_leaf_1_open\": 1}}.")
    out: dict[str, dict] = {}
    for state, values in states.items():
        if not isinstance(state, str) or not state:
            raise _Refused(f"A leaf state needs a name, not {state!r}.")
        if not isinstance(values, dict) or not values \
                or not all(isinstance(key, str) and key for key in values):
            raise _Refused(f"Leaf state '{state}' must be an object of GDL parameter names "
                           f"to values, such as {{\"gs_leaf_1_open\": 1}}, not {values!r}.")
        out[state] = dict(values)
    return out


class _Learning:
    """One learning call: what it placed, and what it has to report."""

    def __init__(self, conn: ArchicadConnection, name: str, kind: str,
                 origin: tuple[float, float], story: tuple[int, float],
                 variants: list[dict]):
        self.conn, self.name, self.kind = conn, name, kind
        self.origin = origin
        self.story = story                   # (index, level) of the story on screen
        self.variants = variants
        self.samples: list[str] = []         # GUIDs of the samples placed, in order
        self.wall: str | None = None
        self.warnings: list[str] = []
        self.note: str | None = None         # what the reply must say above the warnings

    def placed(self) -> list[str]:
        return self.samples + ([self.wall] if self.wall else [])

    def to_delete(self, strays: list[tuple[str, str]]) -> list[str]:
        # Openings before the walls that host them.
        walls = [guid for guid, element_type in strays if element_type == "Wall"]
        return (self.samples + [guid for guid, _ in strays if guid not in walls]
                + ([self.wall] if self.wall else []) + walls)

    def run(self) -> dict:
        if self.kind in ("object", "lamp"):
            return self._objects()
        return self._openings()

    def _objects(self) -> dict:
        command, key = _CREATE[self.kind]
        placed = _object_samples(self.name, self.origin, self.story, self.variants)
        self._create(command, key, placed)
        self._apply_variants(placed)
        footprint, parameters = self._measure(placed)
        png = self._look([p.guid for p in placed])
        return {"png": png, "samples": [p.label for p in placed], "measured": footprint,
                "parameters": parameters}

    def _openings(self) -> dict:
        command, key = _CREATE[self.kind]
        x, y = self.origin
        slots = len(COMBOS) + len(self.variants)
        # With floorIndex, CreateWalls reads zCoordinate as the offset from that
        # story, so leaving it out puts the wall on the story's floor.
        wall = _Placed("temporary wall", {"begCoordinate": {"x": x, "y": y},
                                          "endCoordinate": {"x": x + SPACING * slots, "y": y},
                                          "height": WALL_HEIGHT, "floorIndex": self.story[0]})
        self._create("CreateWalls", "wallsData", [wall], host=True)
        placed = _opening_samples(self.name, self.kind, wall.guid, self.variants)
        tool = self.kind.capitalize()
        try:
            self._create(command, key, placed)
        finally:
            self.warnings.append(
                f"The {tool} tool now holds '{self.name}': Tapir 1.7.2 places a {self.kind} "
                f"by part name through the {tool} tool's settings and does not set them back.")
        self._apply_variants(placed)
        parameters = self._opening_parameters(placed[0])
        png = self._look([wall.guid] + [p.guid for p in placed])
        return {"png": png, "samples": [p.label for p in placed], "measured": None,
                "parameters": parameters}

    def _create(self, command: str, key: str, placed: list[_Placed],
                host: bool = False) -> None:
        """Every GUID the reply names is kept for deletion before a failure is raised."""
        response = self.conn.tapir(command, {key: [p.item for p in placed]})
        entries = response.get("elements") or []
        failed = []
        for i, p in enumerate(placed):
            entry = entries[i] if i < len(entries) and isinstance(entries[i], dict) else {}
            guid = (entry.get("elementId") or {}).get("guid")
            if guid:
                p.guid = guid
                if host:
                    self.wall = guid
                else:
                    self.samples.append(guid)
                continue
            error = entry.get("error") or {}
            failed.append(f"'{p.label}' ({error.get('message', 'no answer for it')})")
        if failed:
            raise _Failed(f"Archicad did not place {', '.join(failed)}.")

    def _apply_variants(self, placed: list[_Placed]) -> None:
        """A variant's values, written after the create; a refusal is reported, the
        sample stays and shows the part without them."""
        varied = [p for p in placed if p.variant]
        if not varied:
            return
        payload = [{"elementId": {"guid": p.guid},
                    "gdlParameters": [{"name": k, "value": v} for k, v in p.variant.items()]}
                   for p in varied]
        stopped = None
        try:
            response = self.conn.tapir("SetGDLParametersOfElements",
                                       {"elementsWithGDLParameters": payload})
            results = response.get("executionResults") or []
        except APIErrorBase as exc:
            results, stopped = [], exc.message
        for i, p in enumerate(varied):
            outcome = results[i] if i < len(results) else None
            if isinstance(outcome, dict) and outcome.get("success") is True:
                continue
            if stopped is not None:
                why = stopped
            elif isinstance(outcome, dict):
                why = (outcome.get("error") or {}).get("message", "refused without a reason")
            else:
                why = "no answer for it"
            self.warnings.append(f"Archicad refused the values of sample '{p.label}' ({why}), "
                                 "so it shows the part without them.")

    def _measure(self, placed: list[_Placed]) -> tuple[dict, list[dict]]:
        """The footprint fitted on the sized samples, and the first one's parameters."""
        sized = placed[:len(SAMPLE_SIZES)]
        mirrored = placed[len(SAMPLE_SIZES)]
        boxes = _boxes(self.conn, [p.guid for p in [*sized, mirrored]])
        taken, parameters = self._sizes_taken(sized)
        samples = []
        for p, box, (a, b) in zip(sized, boxes, taken):
            if box is None:
                self.warnings.append(f"Archicad gave no plan box for sample '{p.label}', so "
                                     "the footprint is fitted without it.")
                continue
            samples.append(Sample(a, b, _from_insertion(box, p), sent=(p.a, p.b)))
        if not samples:
            raise _Failed("Archicad gave no plan box for any sample, so the footprint "
                          "could not be measured.")
        if len(samples) == 1:
            self.warnings.append("Only one size could be measured, so the footprint is "
                                 "fixed: it will not follow the size the part is placed at.")
        self._check_mirror(sized[0], boxes[0], mirrored, boxes[len(SAMPLE_SIZES)])
        return self._fitted(samples), parameters

    def _fitted(self, samples: list[Sample]) -> dict | None:
        """The footprint, each axis fitted against the size its plan box follows; None,
        and the reply's note, when an axis follows neither."""
        fits = {axis: fit_axis(samples, axis) for axis in _AXIS_WORDS}
        for axis, fit in fits.items():
            if fit.against == "sent":
                extent, size = _AXIS_WORDS[axis]
                self.warnings.append(
                    f"The plan {extent} follows the {size} sent, not the {size} the part's "
                    f"GDL reads back, so the {axis} edges of the footprint are fitted "
                    "against the size sent.")
        unfollowed = [axis for axis, fit in fits.items() if fit.edges is None]
        if not unfollowed:
            return {**fits["x"].edges, **fits["y"].edges}
        extents = " and ".join(f"plan {_AXIS_WORDS[axis][0]}" for axis in unfollowed)
        sizes = " and ".join(_AXIS_WORDS[axis][1] for axis in unfollowed)
        grows = "grow" if len(unfollowed) > 1 else "grows"
        self.note = (f"This part's plan size does not follow its own A and B: its {extents} "
                     f"{grows} at another rate than the {sizes} it was sent at and the "
                     f"{sizes} its GDL reads back, so no footprint is recorded and "
                     "placement cannot place it. Place it by coordinates, angle, reflected "
                     "and dimensions, then look with capture_window.")
        return None

    def _sizes_taken(self, sized: list[_Placed]) -> tuple[list[tuple[float, float]], list]:
        """The A and B each sample took, as its GDL reads them; the size sent where the
        read fails."""
        try:
            response = self.conn.tapir("GetGDLParametersOfElements",
                                       {"elements": element_payload([p.guid for p in sized])})
            entries = response.get("gdlParametersOfElements") or []
        except APIErrorBase:
            entries = []
        lists = [_parameters_of(entries[i] if i < len(entries) else None)
                 for i in range(len(sized))]
        taken = []
        for p, parameters in zip(sized, lists):
            a, b = _number(parameters, "A"), _number(parameters, "B")
            if a is None or b is None:
                self.warnings.append(f"The GDL A and B of sample '{p.label}' could not be "
                                     "read, so its footprint is fitted against the size sent.")
            a, b = (p.a if a is None else a), (p.b if b is None else b)
            if abs(a - p.a) > _SAME or abs(b - p.b) > _SAME:
                self.warnings.append(
                    f"Sample '{p.label}' was sent at A {p.a:g}, B {p.b:g} and the part took "
                    f"A {a:g}, B {b:g} (its GDL reads them so): each axis of the footprint "
                    "is fitted against whichever of the two sizes its plan box follows.")
            taken.append((a, b))
        return taken, _named_values(lists[0] or [])

    def _check_mirror(self, first: _Placed, first_box: tuple | None, mirrored: _Placed,
                      mirrored_box: tuple | None) -> None:
        # Placement mirrors a footprint by local x becoming -x (probe, Bed Double 01).
        if first_box is None or mirrored_box is None:
            return
        x_min, y_min, x_max, y_max = _from_insertion(first_box, first)
        expected = (-x_max, y_min, -x_min, y_max)
        seen = _from_insertion(mirrored_box, mirrored)
        if max(abs(s - e) for s, e in zip(seen, expected)) > _MIRROR_TOLERANCE:
            self.warnings.append(
                f"The mirrored sample covers {_rounded(seen)} from its insertion point, not "
                f"{_rounded(expected)}, the mirror image of the first sample: this part does "
                "not mirror about its own y axis, so a mirrored placement may miss its "
                "footprint.")

    def _opening_parameters(self, first: _Placed) -> list[dict]:
        why = None
        try:
            response = self.conn.tapir("GetGDLParametersOfElements",
                                       {"elements": element_payload([first.guid])})
            entries = response.get("gdlParametersOfElements") or []
            entry = entries[0] if entries else None
        except APIErrorBase as exc:
            entry, why = None, exc.message
        parameters = _parameters_of(entry)
        if parameters is None:
            if why is None and isinstance(entry, dict):
                why = (entry.get("error") or {}).get("message")
            self.warnings.append(
                f"The {self.kind}'s GDL parameters could not be read, probably because "
                "Tapir 1.7.2 refuses them for doors and windows placed through the API"
                + (f" ({why})" if why else "") + ". Which sash or leaf a parameter opens "
                "cannot be read from this sample.")
            return []
        return _named_values(parameters)

    def _look(self, guids: list[str]) -> bytes:
        zoomed = fit_in_window(self.conn, guids)
        self.warnings.append(_ZOOMED if zoomed else _NOT_ZOOMED)
        image = capture_window(self.conn)
        if "png" not in image:
            raise _Failed(f"The samples could not be captured: "
                          f"{image.get('error', 'no image came back')}")
        return image["png"]


def _object_samples(name: str, origin: tuple[float, float], story: tuple[int, float],
                    variants: list[dict]) -> list[_Placed]:
    a0, b0 = SAMPLE_SIZES[0]
    specs = [(f"{a} x {b} m", a, b, False, None) for a, b in SAMPLE_SIZES]
    specs.append((f"{a0} x {b0} m mirrored", a0, b0, True, None))
    specs += [(f"{a0} x {b0} m, variant {n}: {_said(v)}", a0, b0, False, v)
              for n, v in enumerate(variants, 1)]
    x, y = origin
    index, level = story
    placed = []
    for label, a, b, mirrored, variant in specs:
        # Without floorIndex Tapir picks the story from z, so z 0 lands on the
        # ground story whatever story is on screen. With it, z is absolute and
        # Tapir keeps z minus the story's level: at the level, on its floor.
        placed.append(_Placed(label, {"libraryPartName": name,
                                      "coordinates": {"x": x, "y": y, "z": level},
                                      "floorIndex": index,
                                      "dimensions": {"x": a, "y": b, "z": SAMPLE_HEIGHT},
                                      "angle": 0.0, "reflected": mirrored},
                              a, b, variant))
        # The next sample stands this one's A plus SPACING on, so neighbours keep
        # apart wherever the part's insertion point sits on it (at least SPACING
        # less the next sample's A).
        x += a + SPACING
    return placed


def _opening_samples(name: str, kind: str, wall: str,
                     variants: list[dict]) -> list[_Placed]:
    height, sill = _OPENING_SIZES[kind]

    def item(slot: int, combo: str) -> dict:
        return {"ownerWallId": {"guid": wall}, "centerOffset": SPACING * (slot + 0.5),
                "width": OPENING_WIDTH, "height": height, "sillHeight": sill,
                "reflected": combo[0] == "t", "oSide": combo[1] == "t",
                "libraryPartName": name}

    placed = [_Placed(f"{combo}: reflected {_word(combo[0])}, oSide {_word(combo[1])}",
                      item(slot, combo))
              for slot, combo in enumerate(COMBOS)]
    placed += [_Placed(f"{COMBOS[0]}, variant {n}: {_said(v)}",
                       item(len(COMBOS) + n - 1, COMBOS[0]), variant=v)
               for n, v in enumerate(variants, 1)]
    return placed


def _bad_arguments(kind: str, variants: list) -> str | None:
    if kind not in KINDS:
        return f"kind must be one of {', '.join(KINDS)}, not {kind!r}. Nothing was placed."
    for n, variant in enumerate(variants, 1):
        if not isinstance(variant, dict) or not variant \
                or not all(isinstance(key, str) and key for key in variant):
            return (f"Variant {n} is not a set of GDL parameter values: give each variant "
                    "as an object of parameter names to values, such as "
                    "{\"gs_leaf_1_open\": 1}. Nothing was placed.")
    return None


def _loaded_part(conn: ArchicadConnection, name: str) -> tuple[str | None, str | None,
                                                                list[str]]:
    """(the main GUID of the loaded part named name, why there is none, warnings)."""
    try:
        response = conn.tapir("GetAvailableLibraryParts", {})
    except APIErrorBase as exc:
        return None, (f"The loaded library parts could not be listed ({exc.message}). "
                      "Nothing was placed."), []
    entries = response.get("libraryParts")
    if not isinstance(entries, list):
        return None, "Archicad listed no library parts. Nothing was placed.", []
    # Pictures are image files in the library, not parts an element can use.
    parts = [e for e in entries if isinstance(e, dict) and e.get("typeId") != "Picture"]
    matches = [e for e in parts if e.get("documentName") == name]
    if matches:
        if not matches[0].get("guid"):
            return None, (f"Archicad lists '{name}' without its GUID, so no record can be "
                          "tied to it. Nothing was placed."), []
        guids = list(dict.fromkeys(str(e.get("guid")) for e in matches))
        notes = [] if len(guids) == 1 else [
            f"{len(guids)} loaded parts are named '{name}' ({', '.join(guids)}); the record "
            f"is tied to the first, {guids[0]}."]
        return guids[0], None, notes
    for e in parts:
        file_name = str(e.get("fileName"))
        spellings = (str(e.get("documentName")), file_name, file_name.removesuffix(".gsm"))
        if name.casefold() in (s.casefold() for s in spellings):
            return None, (f"The library spells '{name}' as '{e.get('documentName')}': learn "
                          "it under that name, as Archicad may not match another spelling. "
                          "Nothing was placed."), []
    names = sorted({str(e.get("documentName")) for e in parts})
    reason = f"There is no loaded library part named '{name}'{closest_names(name, names)}."
    unread = response.get("skippedCount") or 0
    if unread:
        reason += f" {unread} library entries could not be read, and it may be one of them."
    return None, reason + " Nothing was placed.", []


def _boxes(conn: ArchicadConnection, guids: list[str]) -> list[tuple | None]:
    """Each element's plan box (xMin, yMin, xMax, yMax) in world metres, in order; None
    where Archicad gives none (a PolyLine answers 7203)."""
    out: list[tuple | None] = []
    for start in range(0, len(guids), BOX_CHUNK):
        chunk = guids[start:start + BOX_CHUNK]
        response = conn.official("API.Get2DBoundingBoxes", {"elements": element_payload(chunk)})
        items = response.get("boundingBoxes2D") or []
        out += [_box(items[i] if i < len(items) else None) for i in range(len(chunk))]
    return out


def _box(item: object) -> tuple | None:
    box = item.get("boundingBox2D") if isinstance(item, dict) else None
    if not isinstance(box, dict):
        return None
    try:
        return tuple(float(box[key]) for key in ("xMin", "yMin", "xMax", "yMax"))
    except (KeyError, TypeError, ValueError):
        return None


def _origin(boxes: list[tuple | None]) -> tuple[float, float]:
    found = [box for box in boxes if box is not None]
    if not found:
        return 0.0, 0.0
    return max(box[2] for box in found) + CLEARANCE, min(box[1] for box in found)


def _current_story(conn: ArchicadConnection) -> tuple[int, float] | None:
    """(index, level) of the story the floor plan shows, or None if Archicad does not say."""
    stories = conn.tapir("GetStories")
    index = stories.get("actStory")
    for story in stories.get("stories") or []:
        if isinstance(story, dict) and story.get("index") == index \
                and isinstance(story.get("level"), (int, float)):
            return index, float(story["level"])
    return None


def _strays(conn: ArchicadConnection, before: list[str], placed: list[str], kind: str,
            origin_x: float) -> list[tuple[str, str]]:
    """(guid, type) of samples in the model that no reply named: a create that failed
    part way can leave one.

    Only an element that appeared while learning, is of a type this call places, and
    lies past the old model extent counts. Anything else that appeared is the user's
    (drawn meanwhile, or received in Teamwork) and is left alone. Runs in a finally,
    so it never raises: a failed read finds none.
    """
    try:
        known = {guid.upper() for guid in [*before, *placed]}
        appeared = [g for g in get_all_element_ids(conn) if g.upper() not in known]
        if not appeared:
            return []
        past = [g for g, box in zip(appeared, _boxes(conn, appeared))
                if box is not None and box[0] >= origin_x - CLEARANCE / 2]
        types = _fetch_types(conn, past) if past else {}
    except Exception:
        return []
    return [(g, types[g]) for g in past if types.get(g) in _SAMPLED_TYPES[kind]]


def _remove(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    """guid -> why it is still in the model, after deleting everything learning placed.

    It runs in a finally, so it never raises.
    """
    left: dict[str, str] = {}
    if not guids:
        return left
    response, stopped = {}, None
    try:
        response = conn.tapir("DeleteElements", {"elements": element_payload(guids)}) or {}
    except Exception as exc:     # an unanswered delete is reported, never raised
        stopped = exc
    try:
        remaining, reason = present(conn, guids), None
    except Exception:
        remaining, reason = set(guids), _UNCHECKED
    refused = _refused_deletions(response, guids)
    for guid in guids:
        if guid in remaining:
            left[guid] = refused.get(guid) or reason or _survivor_reason(response, stopped)
    return left


def _record(earlier: PartRecord | None, name: str, guid: str, kind: str, tapir: str | None,
            footprint: dict | None) -> PartRecord:
    learned = date.today().isoformat()
    if earlier is not None and identity_problem(earlier, guid) is None:
        # The same part learned again: what the agent recorded about it stays.
        return replace(earlier, guid=guid, kind=kind, learned=learned, tapir=tapir,
                       footprint=footprint)
    return PartRecord(name=name, guid=guid, kind=kind, learned=learned, tapir=tapir,
                      footprint=footprint)


def _older(version: str | None, minimum: tuple[int, ...]) -> bool:
    """True only for a version that reads older; an unknown one is let through."""
    numbers = [int(n) for n in re.findall(r"\d+", version or "")[:3]]
    return bool(numbers) and tuple((numbers + [0, 0, 0])[:3]) < minimum


def _from_insertion(box: tuple, placed: _Placed) -> tuple[float, float, float, float]:
    x, y = placed.item["coordinates"]["x"], placed.item["coordinates"]["y"]
    return box[0] - x, box[1] - y, box[2] - x, box[3] - y


def _parameters_of(entry: object) -> list | None:
    parameters = entry.get("parameters") if isinstance(entry, dict) else None
    return parameters if isinstance(parameters, list) and parameters else None


def _number(parameters: list | None, name: str) -> float | None:
    for parameter in parameters or []:
        if isinstance(parameter, dict) and str(parameter.get("name")).casefold() == name.casefold():
            value = parameter.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return None
    return None


def _named_values(parameters: list) -> list[dict]:
    return [{"name": p.get("name"), "type": p.get("type"), "value": p.get("value")}
            for p in parameters if isinstance(p, dict)]


def _said(variant: dict) -> str:
    return ", ".join(f"{key} = {json.dumps(value)}" for key, value in variant.items())


def _word(flag: str) -> str:
    return "true" if flag == "t" else "false"


def _rounded(box: tuple) -> list[float]:
    return [round(value, 3) + 0.0 for value in box]
