import pytest

from archicad_mcp.core import road_arrow as ra


def test_the_table():
    assert len(ra.TABLE["arrows"]) == 31 and ra.TABLE["defaults"]["arrowType"] == "5411 Straight"
    assert ra.TABLE["by_speed"][-1] == [None, "12.00 m"]


def test_the_arrow_name_matches_without_case_and_custom_is_not_an_arrow():
    assert ra.arrow_name("5412 LEFT") == "5412 Left"
    with pytest.raises(ra.RoadArrowError, match="5411 Straight"):
        ra.arrow_name("nope")
    with pytest.raises(ra.RoadArrowError):
        ra.arrow_name("Custom")


def test_speed_picks_the_size():
    for v, want in ((30, "3.00 m"), (31, "5.00 m"), (50, "5.00 m"), (90, "7.50 m"), (91, "12.00 m")):
        assert ra.pick_size("5411 Straight", "5.00 m", speed_kmh=v) == (want, [])


def test_a_fixed_length_arrow_keeps_its_length_and_warns():
    size, warnings = ra.pick_size("5417 Wrong way", "10.00 m", speed_kmh=50)
    assert size == "10.00 m" and "10.00 m" in warnings[0]
    size, warnings = ra.pick_size("5451 Lane ending, move right", "junk", speed_kmh=50)
    assert size == "5.00 by 2.90 m" and "5.00 by 2.90 m" in warnings[0]


def test_no_size_given_follows_the_parts_size_fix():
    assert ra.pick_size("5417 Wrong way", "5.00 m") == ("10.00 m", [])
    assert ra.pick_size("5451 Lane ending, move right", "5.00 m")[0] == "5.00 by 2.90 m"
    assert ra.pick_size("5411 Straight", "7.50 m") == ("7.50 m", [])
    assert ra.pick_size("5411 Straight", "Custom") == ("Custom", [])
    assert ra.pick_size("5411 Straight", "nonsense")[0] == "3.00 m"      # 0 m asked: the nearest is the shortest
    assert ra.pick_size("5411 Straight", "6.25 m")[0] == "7.50 m"        # a tie goes to the longer


def test_a_size_is_matched_without_case_and_must_be_offered():
    assert ra.pick_size("5451 Lane ending, move right", "5.00 m", size="custom") == ("Custom", [])
    assert ra.pick_size("5411 Straight", "5.00 m", size="12.00 M")[0] == "12.00 m"
    with pytest.raises(ra.RoadArrowError, match="size.*3.00 m"):
        ra.pick_size("5411 Straight", "5.00 m", size="4.00 m")


@pytest.mark.parametrize("kwargs, argument", [
    (dict(size="5.00 m", speed_kmh=50), "speed_kmh"),
    (dict(custom_length=5.0, speed_kmh=50), "speed_kmh"),
    (dict(custom_length=5.0, size="5.00 m"), "size"),
    (dict(speed_kmh=0), "speed_kmh"),
    (dict(speed_kmh=-3), "speed_kmh"),
    (dict(speed_kmh=True), "speed_kmh"),
    (dict(speed_kmh="fast"), "speed_kmh"),
    (dict(custom_length=0.1), "custom_length"),
    (dict(custom_length=51), "custom_length"),
    (dict(custom_width=0.01), "custom_width"),
    (dict(custom_width=21), "custom_width"),
    (dict(custom_width=True), "custom_width"),
])
def test_bad_combinations_and_values_are_refused_naming_the_argument(kwargs, argument):
    with pytest.raises(ra.RoadArrowError, match=argument):
        ra.pick_size("5411 Straight", "5.00 m", **kwargs)


def test_a_custom_value_means_custom():
    assert ra.pick_size("5411 Straight", "5.00 m", custom_length=6.0) == ("Custom", [])
    assert ra.pick_size("5411 Straight", "5.00 m", size="Custom", custom_width=1.0) == ("Custom", [])


def test_a_named_size_gives_its_own_row():
    d = ra.dimensions("5412 Left", "7.50 m", None, None)
    assert d == {"length": 7.5, "width": 1.05, "u_lo": -0.975, "u_hi": 0.075}


def test_custom_reach_scales_from_the_nearest_drawn_size():
    d = ra.dimensions("5412 Left", "Custom", 5.0, 2.1)
    assert d["length"] == 5.0 and d["width"] == 2.1
    assert d["u_lo"] == pytest.approx(-0.975 * 2) and d["u_hi"] == pytest.approx(0.075 * 2)


def test_custom_defaults_come_from_the_nearest_drawn_row():
    d = ra.dimensions("5412 Left", "Custom", 7.0, None)          # 7.50 m is nearest; 3.00 m is not drawn
    assert d["length"] == 7.0 and d["width"] == pytest.approx(1.05)
    d = ra.dimensions("5412 Left", "Custom", None, 0.5)          # no length: the row's own
    assert d["length"] == 5.0 and d["u_lo"] == pytest.approx(-0.975 * 0.5 / 1.05)
    assert ra.dimensions("5412 Left", "Custom", 6.25, None)["length"] == 6.25   # tie: 7.50 m, same width


def test_a_fixed_length_arrow_with_a_speed_follows_the_size_fix_and_keeps_custom(monkeypatch):
    rows = {"3.00 m": {"length": 3.0, "width": 1.0}, "10.00 m": {"length": 10.0, "width": 1.0},
            "20.00 m": {"length": 20.0, "width": 1.0}}
    monkeypatch.setitem(ra.TABLE["arrows"], "Fixed", {"by_speed": False, "sizes": rows})
    size, warnings = ra.pick_size("Fixed", "18.00 m", speed_kmh=50)
    assert size == "20.00 m" == ra.pick_size("Fixed", "18.00 m")[0] and "20.00 m" in warnings[0]
    assert ra.pick_size("Fixed", "Custom", speed_kmh=50)[0] == "Custom"
    assert ra.pick_size("5417 Wrong way", "Custom", speed_kmh=50)[0] == "Custom"


@pytest.mark.parametrize("kwargs, argument", [
    (dict(speed_kmh=float("nan")), "speed_kmh"), (dict(speed_kmh=float("inf")), "speed_kmh"),
    (dict(custom_length=float("nan")), "custom_length"), (dict(custom_length=float("inf")), "custom_length"),
    (dict(custom_width=float("nan")), "custom_width"), (dict(custom_width=float("inf")), "custom_width")])
def test_values_that_are_not_finite_are_refused(kwargs, argument):
    with pytest.raises(ra.RoadArrowError, match=argument):
        ra.pick_size("5411 Straight", "5.00 m", **kwargs)
