from datetime import datetime, timezone, timedelta
from server import _filter_altitude_outliers


def row(offset_secs, alt):
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_secs)
    return {"ts": ts.isoformat(), "alt_baro": alt}


def row_ms(offset_ms, alt):
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(milliseconds=offset_ms)
    return {"ts": ts.isoformat(), "alt_baro": alt}


def test_no_outliers_unchanged():
    # ~600 ft/min climb — well within limit
    rows = [row(i * 10, 1000 + i * 100) for i in range(5)]
    assert _filter_altitude_outliers(rows) == rows


def test_interior_spike_removed():
    # 50000 ft jumps vs both neighbors in 10s → ~240000 ft/min each
    rows = [row(0, 1000), row(10, 1010), row(20, 50000), row(30, 1020), row(40, 1030)]
    result = _filter_altitude_outliers(rows)
    assert len(result) == 4
    assert all(r["alt_baro"] != 50000 for r in result)


def test_neighbors_of_spike_not_removed():
    rows = [row(0, 1000), row(10, 1010), row(20, 50000), row(30, 1020), row(40, 1030)]
    result = _filter_altitude_outliers(rows)
    alts = [r["alt_baro"] for r in result]
    assert 1010 in alts
    assert 1020 in alts


def test_first_point_outlier_removed():
    rows = [row(0, 50000), row(10, 1000), row(20, 1010)]
    result = _filter_altitude_outliers(rows)
    assert len(result) == 2
    assert result[0]["alt_baro"] == 1000


def test_last_point_outlier_removed():
    rows = [row(0, 1000), row(10, 1010), row(20, 50000)]
    result = _filter_altitude_outliers(rows)
    assert len(result) == 2
    assert result[-1]["alt_baro"] == 1010


def test_single_row_unchanged():
    rows = [row(0, 1000)]
    assert _filter_altitude_outliers(rows) == rows


def test_empty_unchanged():
    assert _filter_altitude_outliers([]) == []


def test_spike_with_sub_second_neighbors():
    # Spike at 38.994s is only 4ms after previous and 796ms before next —
    # both neighbors are sub-second apart, which previously caused the check
    # to be skipped entirely (dt >= 1 guard). Fixed by using dt > 0.
    rows = [
        row_ms(38_965, 2300),
        row_ms(38_990, 2300),
        row_ms(38_994, 11325),  # spike
        row_ms(39_790, 2300),
        row_ms(41_858, 2300),
    ]
    result = _filter_altitude_outliers(rows)
    assert len(result) == 4
    assert all(r["alt_baro"] != 11325 for r in result)
