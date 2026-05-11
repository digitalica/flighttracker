from server import _compute_agl_offset


def rows(*alts):
    return [{"alt_baro": a} for a in alts]


def test_example_positive():
    # two lowest values are 100 and 200; avg of (100,200,200,200) = 175 → rounds to 200
    assert _compute_agl_offset(rows(100, 200, 200, 200)) == 200


def test_example_negative():
    # two lowest values are -300 and -200; avg of all five = -220 → rounds to -200
    assert _compute_agl_offset(rows(-200, -200, -300, -200, -200)) == -200


def test_single_row():
    assert _compute_agl_offset(rows(150)) == 200


def test_empty():
    assert _compute_agl_offset([]) == 0


def test_all_same():
    assert _compute_agl_offset(rows(200, 200, 200)) == 200


def test_rounds_to_nearest_100():
    # two lowest: 50 and 100; avg = 75 → rounds to 100
    assert _compute_agl_offset(rows(50, 100, 500, 500)) == 100


def test_ignores_altitudes_at_or_above_1000():
    # 1000 ft and above are excluded; only 200 qualifies
    assert _compute_agl_offset(rows(200, 1000, 2000, 5000)) == 200


def test_all_above_1000_returns_zero():
    assert _compute_agl_offset(rows(1000, 2000, 5000)) == 0


def test_high_altitude_flight_ignored():
    # cruise data should not skew the offset; values below 1000 ft win
    assert _compute_agl_offset(rows(100, 200, 3000, 6000, 8000)) == 200
