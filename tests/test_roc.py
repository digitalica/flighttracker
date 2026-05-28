from datetime import datetime, timezone, timedelta
from server import _compute_roc


def row(offset_secs, alt):
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_secs)
    return {"ts": ts.isoformat(), "alt_baro": alt}


def test_empty():
    assert _compute_roc([]) == []


def test_single_row():
    assert _compute_roc([row(0, 1000)]) == [0]


def test_steady_climb():
    # 600 ft/min = 10 ft/s; points every 5s, window=10s
    rows = [row(i * 5, 1000 + i * 50) for i in range(5)]
    result = _compute_roc(rows, window_secs=10)
    assert all(r == 600 for r in result)


def test_level_flight():
    rows = [row(i * 5, 2000) for i in range(5)]
    result = _compute_roc(rows, window_secs=10)
    assert all(r == 0 for r in result)


def test_descent():
    # -600 ft/min
    rows = [row(i * 5, 2000 - i * 50) for i in range(5)]
    result = _compute_roc(rows, window_secs=10)
    assert all(r == -600 for r in result)


def test_window_limits_range():
    # points at 0, 5, 10, 15, 20s; big jump only outside the 10s window
    # first 3 points climb at 600 ft/min, then sudden level-off
    rows = [
        row(0,  1000),
        row(5,  1050),
        row(10, 1100),
        row(15, 1100),
        row(20, 1100),
    ]
    result = _compute_roc(rows, window_secs=10)
    # middle point (index 2, t=10) spans t=0..20 but window clips to t=0..20
    # lo=0 (t=0 >= 10-10), hi=4 (t=20 <= 10+10): (1100-1000)/20*60 = 300
    assert result[2] == 300
    # last point (index 4, t=20): lo clips to index 2 (t=10), hi=4: (1100-1100)/10*60 = 0
    assert result[4] == 0
