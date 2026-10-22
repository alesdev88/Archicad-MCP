from math import cos, pi, sin

import pytest

from archicad_mcp.core.part_placement import (
    Sample,
    fit_footprint,
    footprint_at,
    object_fields,
    opening_fields,
)
from archicad_mcp.core.part_records import PartRecord


def record(**overrides) -> PartRecord:
    fields = dict(name="Part", guid="6B4F1A52-0C3E-4D1B-9A77-2E5C8D90F311",
                  kind="object", learned="2026-10-22", head="+y", one_sided=True)
    fields.update(overrides)
    return PartRecord(**fields)


# Corner insertion: the box runs from the insertion point to (A, B).
BED = record(head="+y", footprint={"xMin": [0, 0], "xMax": [1, 0], "yMin": [0, 0], "yMax": [1, 0]})
CENTRED = record(head="+y", footprint={"xMin": [-0.5, 0], "xMax": [0.5, 0],
                                       "yMin": [-0.5, 0], "yMax": [0.5, 0]})
# Centred across, the foot end at the insertion point.
FOOT_CENTRED = record(head="+y", footprint={"xMin": [-0.5, 0], "xMax": [0.5, 0],
                                            "yMin": [0, 0], "yMax": [1, 0]})
# The same corner insertion, the head along the part's own x.
SIDEWAYS = record(head="+x", footprint={"xMin": [0, 0], "xMax": [1, 0],
                                        "yMin": [0, 0], "yMax": [1, 0]})

# The door table measured live for the part "Door".
DOOR_COMBOS = {"ff": ["begin", "right"], "tf": ["end", "right"],
               "ft": ["end", "left"], "tt": ["begin", "left"]}
DOOR = record(kind="door", head=None, one_sided=None, combos=DOOR_COMBOS, leaves=1)


def assert_footprint(footprint: dict, expected: dict) -> None:
    assert set(footprint) == set(expected)
    for edge, line in expected.items():
        assert footprint[edge] == pytest.approx(line), edge


def placed_centre(part: PartRecord, fields: dict, mirrored: bool = False) -> tuple[float, float]:
    """The centre of the footprint in the plan: the local centre turned back by the angle."""
    a, b = fields["dimensions"]["x"], fields["dimensions"]["y"]
    x0, y0, x1, y1 = footprint_at(part.footprint, a, b)
    if mirrored:
        x0, x1 = -x1, -x0
    local_x, local_y = (x0 + x1) / 2, (y0 + y1) / 2
    c, s = cos(fields["angle"]), sin(fields["angle"])
    return (fields["coordinates"]["x"] + local_x * c - local_y * s,
            fields["coordinates"]["y"] + local_x * s + local_y * c)


def placed_head(part: PartRecord, fields: dict, mirrored: bool = False) -> tuple[float, float]:
    """Where the part's head points in the plan: the (mirrored) local head turned by the angle."""
    local_x, local_y = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[part.head]
    if mirrored:
        local_x = -local_x
    c, s = cos(fields["angle"]), sin(fields["angle"])
    return (local_x * c - local_y * s, local_x * s + local_y * c)


# --- fitting and reading a footprint ---------------------------------------------------

def test_fit_recovers_a_corner_and_a_centre_insertion():
    corner = fit_footprint([Sample(1.0, 2.0, (0.0, 0.0, 1.0, 2.0)),
                            Sample(1.8, 1.2, (0.0, 0.0, 1.8, 1.2))])
    assert_footprint(corner, {"xMin": [0, 0], "xMax": [1, 0], "yMin": [0, 0], "yMax": [1, 0]})

    centre = fit_footprint([Sample(1.0, 2.0, (-0.5, -1.0, 0.5, 1.0)),
                            Sample(1.8, 1.2, (-0.9, -0.6, 0.9, 0.6))])
    assert_footprint(centre, {"xMin": [-0.5, 0], "xMax": [0.5, 0],
                              "yMin": [-0.5, 0], "yMax": [0.5, 0]})


def test_fit_keeps_a_constant_offset():
    # xMin = 0.5 * A - 0.2 (e.g. a headboard), yMax = B + 0.1.
    samples = [Sample(1.0, 2.0, (0.3, 0.0, 1.0, 2.1)),
               Sample(2.0, 3.0, (0.8, 0.0, 2.0, 3.1)),
               Sample(3.0, 1.0, (1.3, 0.0, 3.0, 1.1))]
    assert_footprint(fit_footprint(samples), {"xMin": [0.5, -0.2], "xMax": [1, 0],
                                              "yMin": [0, 0], "yMax": [1, 0.1]})


def test_fit_with_equal_a_fixes_the_x_edges():
    # The part clamps its width: A stays 1.0 however it is asked.
    footprint = fit_footprint([Sample(1.0, 1.0, (-0.5, 0.0, 0.5, 1.0)),
                               Sample(1.0, 2.0, (-0.5, 0.0, 0.5, 2.0))])
    assert_footprint(footprint, {"xMin": [0, -0.5], "xMax": [0, 0.5],
                                 "yMin": [0, 0], "yMax": [1, 0]})


def test_fit_with_equal_b_fixes_the_y_edges():
    # Bed Double 01 refuses B below 1.2.
    footprint = fit_footprint([Sample(1.0, 1.2, (0.0, 0.0, 1.0, 1.2)),
                               Sample(1.8, 1.2, (0.0, 0.0, 1.8, 1.2))])
    assert_footprint(footprint, {"xMin": [0, 0], "xMax": [1, 0],
                                 "yMin": [0, 0], "yMax": [0, 1.2]})


def test_fit_with_one_sample_fixes_every_edge():
    footprint = fit_footprint([Sample(1.0, 1.2, (0.0, 0.0, 1.0, 1.2))])
    assert_footprint(footprint, {"xMin": [0, 0], "xMax": [0, 1.0],
                                 "yMin": [0, 0], "yMax": [0, 1.2]})


def test_fit_without_samples_is_an_error():
    with pytest.raises(ValueError):
        fit_footprint([])


def test_fit_drops_float_noise_and_negative_zero():
    footprint = fit_footprint([Sample(1.0, 2.0, (0.0, 0.0, 1.0000000001, 2.0)),
                               Sample(1.8, 1.2, (0.0, 0.0, 1.8, 1.2))])
    assert footprint["xMax"] == [1.0, 0.0]
    assert footprint["xMin"] == [0.0, 0.0]
    assert str(footprint["xMin"][1]) == "0.0"     # not -0.0


def test_footprint_at_reads_a_box_at_a_size():
    footprint = {"xMin": [-0.5, 0], "xMax": [0.5, 0], "yMin": [0, 0.1], "yMax": [1, 0.1]}
    assert footprint_at(footprint, 1.8, 2.0) == pytest.approx((-0.9, 0.1, 0.9, 2.1))


# --- objects ---------------------------------------------------------------------------

@pytest.mark.parametrize("head, angle, insertion", [
    ({"x": 0, "y": 1}, 0.0, (4.1, 4.0)),            # local centre (0.9, 1.0), turned by 0
    ({"x": -1, "y": 0}, pi / 2, (6.0, 4.1)),        # (0.9, 1.0) turned by 90 deg is (-1.0, 0.9)
    ({"x": 0, "y": -1}, pi, (5.9, 6.0)),            # turned by 180 deg is (-0.9, -1.0)
    ({"x": 1, "y": 0}, 3 * pi / 2, (4.0, 5.9)),     # turned by 270 deg is (1.0, -0.9)
])
def test_bed_covers_its_rectangle_with_the_head_each_way(head, angle, insertion):
    f = object_fields(BED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5, head=head,
                      mirrored=False)
    assert f["angle"] == pytest.approx(angle)
    assert f["dimensions"] == {"x": 1.8, "y": 2.0, "z": 0.5}
    assert f["reflected"] is False
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx(insertion)
    assert f["coordinates"]["z"] == 0.0
    assert placed_centre(BED, f) == pytest.approx((5, 5))
    assert placed_head(BED, f) == pytest.approx((head["x"], head["y"]))


def test_angle_stays_inside_zero_to_two_pi():
    for x, y in [(0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1e-12), (1e-14, 1), (-3, -0.001)]:
        f = object_fields(BED, {"x": 0, "y": 0}, length=2.0, width=1.8, height=0.5,
                          head={"x": x, "y": y}, mirrored=False)
        assert 0.0 <= f["angle"] < 2 * pi


def test_a_head_that_is_not_unit_length_gives_the_same_angle():
    f = object_fields(BED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": -7, "y": 0}, mirrored=False)
    assert f["angle"] == pytest.approx(pi / 2)


def test_centre_insertion_lands_on_the_centre():
    for head in ({"x": 0, "y": 1}, {"x": -1, "y": 0}, {"x": 0, "y": -1}, {"x": 1, "y": 0}):
        f = object_fields(CENTRED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                          head=head, mirrored=False)
        assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((5, 5))

    # Foot end at the insertion point: the centre sits half the length up the head.
    f = object_fields(FOOT_CENTRED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": -1, "y": 0}, mirrored=False)
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((6.0, 5.0))


def test_centre_may_carry_a_height():
    f = object_fields(BED, {"x": 5, "y": 5, "z": 0.3}, length=2.0, width=1.8, height=0.5,
                      head={"x": 0, "y": 1}, mirrored=False)
    assert f["coordinates"]["z"] == 0.3


def test_head_along_local_x_swaps_length_and_width():
    f = object_fields(SIDEWAYS, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": 1, "y": 0}, mirrored=False)
    assert f["angle"] == pytest.approx(0.0)
    assert f["dimensions"] == {"x": 2.0, "y": 1.8, "z": 0.5}      # A is the length now
    # Local centre (1.0, 0.9), no turn.
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((4.0, 4.1))

    f = object_fields(SIDEWAYS, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": 0, "y": 1}, mirrored=False)
    assert f["angle"] == pytest.approx(pi / 2)
    assert f["dimensions"] == {"x": 2.0, "y": 1.8, "z": 0.5}
    # Local centre (1.0, 0.9) turned by 90 deg is (-0.9, 1.0).
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((5.9, 4.0))
    assert placed_centre(SIDEWAYS, f) == pytest.approx((5, 5))


def test_mirrored_flips_local_x():
    # x becomes -x and y stays: the box (0, 0)-(1.8, 2.0) becomes (-1.8, 0)-(0, 2.0),
    # local centre (-0.9, 1.0).
    f = object_fields(BED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": 0, "y": 1}, mirrored=True)
    assert f["reflected"] is True
    assert f["angle"] == pytest.approx(0.0)            # the head is along y, which stays
    assert f["dimensions"] == {"x": 1.8, "y": 2.0, "z": 0.5}
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((5.9, 4.0))
    assert placed_centre(BED, f, mirrored=True) == pytest.approx((5, 5))
    assert placed_head(BED, f, mirrored=True) == pytest.approx((0, 1))


def test_mirrored_head_along_x_turns_the_other_way():
    # Mirrored, the head along +x points along -x, so reaching +x takes a half turn
    # (unmirrored it takes none). Local centre (-1.0, 0.9) turned by 180 deg is (1.0, -0.9).
    f = object_fields(SIDEWAYS, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": 1, "y": 0}, mirrored=True)
    assert f["angle"] == pytest.approx(pi)
    assert f["dimensions"] == {"x": 2.0, "y": 1.8, "z": 0.5}
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((4.0, 5.9))
    assert placed_centre(SIDEWAYS, f, mirrored=True) == pytest.approx((5, 5))
    assert placed_head(SIDEWAYS, f, mirrored=True) == pytest.approx((1, 0))

    # Towards +y: from -x that is a quarter turn clockwise, so 270 deg.
    # Local centre (-1.0, 0.9) turned by 270 deg is (0.9, 1.0).
    f = object_fields(SIDEWAYS, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5,
                      head={"x": 0, "y": 1}, mirrored=True)
    assert f["angle"] == pytest.approx(3 * pi / 2)
    assert (f["coordinates"]["x"], f["coordinates"]["y"]) == pytest.approx((4.1, 4.0))
    assert placed_centre(SIDEWAYS, f, mirrored=True) == pytest.approx((5, 5))
    assert placed_head(SIDEWAYS, f, mirrored=True) == pytest.approx((0, 1))


def test_a_record_without_footprint_or_head_is_an_error():
    for part in (record(footprint=None), record(footprint=BED.footprint, head=None)):
        with pytest.raises(ValueError):
            object_fields(part, {"x": 0, "y": 0}, length=2.0, width=1.8, height=0.5,
                          head={"x": 0, "y": 1}, mirrored=False)


def test_a_zero_head_is_an_error():
    with pytest.raises(ValueError):
        object_fields(BED, {"x": 0, "y": 0}, length=2.0, width=1.8, height=0.5,
                      head={"x": 0, "y": 0}, mirrored=False)


# --- doors and windows -----------------------------------------------------------------

def test_hinge_and_swing_pick_the_recorded_combo():
    # Wall (0, 0) to (10, 0), the opening centred 4.0 along it. The hinge is at 4.6, past
    # the centre: "end". It opens to -y, which is right of the wall's direction: "right".
    # The Door table has end + right at "tf": reflected on, oSide off.
    assert opening_fields(DOOR, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0,
                          hinge_near={"x": 4.6, "y": 0}, opens_toward={"x": 4, "y": -1}) \
        == {"reflected": True, "oSide": False}


def test_every_door_combo_is_reachable():
    wall = ({"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0)
    cases = {                                  # hinge x, opens y -> flags
        (3.4, -1): {"reflected": False, "oSide": False},    # begin, right: ff
        (4.6, -1): {"reflected": True, "oSide": False},     # end, right: tf
        (4.6, 1): {"reflected": False, "oSide": True},      # end, left: ft
        (3.4, 1): {"reflected": True, "oSide": True},       # begin, left: tt
    }
    for (hinge_x, opens_y), flags in cases.items():
        assert opening_fields(DOOR, *wall, hinge_near={"x": hinge_x, "y": 0},
                              opens_toward={"x": 4, "y": opens_y}) == flags


def test_a_window_reads_its_own_table():
    window = record(kind="window", head=None, one_sided=None,
                    combos={"ff": ["begin", "left"], "tf": ["end", "left"],
                            "ft": ["begin", "right"], "tt": ["end", "right"]})
    # End + right is "tt" in this table.
    assert opening_fields(window, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0,
                          hinge_near={"x": 4.6, "y": 0}, opens_toward={"x": 4, "y": -1}) \
        == {"reflected": True, "oSide": True}


def test_a_wall_drawn_right_to_left_keeps_hinge_and_swing_right():
    # The same physical door twice: hinge at x = 5.4, the centre of the opening at x = 6.0,
    # swinging to -y.
    # Wall drawn left to right: the centre is 6.0 along it, the hinge at 5.4 is before it
    # ("begin"), -y is right of +x ("right"): ff.
    assert opening_fields(DOOR, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 6.0,
                          hinge_near={"x": 5.4, "y": 0}, opens_toward={"x": 6, "y": -1}) \
        == {"reflected": False, "oSide": False}
    # Wall drawn right to left from (10, 0): the centre is 4.0 along it, the hinge at 5.4 is
    # 4.6 along ("end"), and -y is left of -x ("left"): ft. The flags differ because they
    # are read along the wall's own begin-to-end direction.
    assert opening_fields(DOOR, {"x": 10, "y": 0}, {"x": 0, "y": 0}, 4.0,
                          hinge_near={"x": 5.4, "y": 0}, opens_toward={"x": 6, "y": -1}) \
        == {"reflected": False, "oSide": True}


def test_a_wall_that_is_not_along_x():
    # Wall (2, 1) to (2, 11): direction +y. The centre is 4.0 along it, at y = 5. The hinge
    # at y = 4.4 is 3.4 along ("begin"); +x is right of +y ("right"): ff.
    assert opening_fields(DOOR, {"x": 2, "y": 1}, {"x": 2, "y": 11}, 4.0,
                          hinge_near={"x": 2, "y": 4.4}, opens_toward={"x": 3, "y": 5}) \
        == {"reflected": False, "oSide": False}
    # Wall (0, 0) to (6, 8): direction (0.6, 0.8), length 10. The hinge (3.6, 4.8) is 6.0
    # along ("end", centre 4.0); (-1, 7) is at cross 0.6 * 7 - 0.8 * (-1) = 5.0, left: ft.
    assert opening_fields(DOOR, {"x": 0, "y": 0}, {"x": 6, "y": 8}, 4.0,
                          hinge_near={"x": 3.6, "y": 4.8}, opens_toward={"x": -1, "y": 7}) \
        == {"reflected": False, "oSide": True}


def test_no_recorded_combo_for_that_hinge_and_swing_is_an_error():
    half = record(kind="door", head=None, one_sided=None,
                  combos={"ff": ["begin", "left"], "tf": ["end", "left"]})
    with pytest.raises(ValueError):             # begin + right is not in the table
        opening_fields(half, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0,
                       hinge_near={"x": 3.4, "y": 0}, opens_toward={"x": 4, "y": -1})
    unlearned = record(kind="door", head=None, one_sided=None, combos=None)
    with pytest.raises(ValueError):
        opening_fields(unlearned, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0,
                       hinge_near={"x": 4.6, "y": 0}, opens_toward={"x": 4, "y": -1})


def test_a_wall_without_length_is_an_error():
    with pytest.raises(ValueError):
        opening_fields(DOOR, {"x": 3, "y": 3}, {"x": 3, "y": 3}, 0.0,
                       hinge_near={"x": 3, "y": 3}, opens_toward={"x": 3, "y": 4})
