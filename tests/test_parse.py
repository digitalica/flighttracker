import os
import sys
import tempfile
from pathlib import Path

# Redirect DB_PATH so importing server doesn't touch the real database
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

sys.path.insert(0, str(Path(__file__).parent.parent / "website"))
from server import _parse_sbs_line  # noqa: E402


# Lines that carry no altitude and no position — should return None
def test_type8_squawk_change_returns_none():
    line = "MSG,8,1,1,484763,1,2026/05/09,07:07:30.523,2026/05/09,07:07:30.529,,,,,,,,,,,,"
    assert _parse_sbs_line(line) is None


def test_type6_surveillance_alt_returns_none():
    line = "MSG,6,1,1,484763,1,2026/05/09,07:12:55.565,2026/05/09,07:12:55.587,PHTGC   ,,,,,,,4250,0,0,0,"
    assert _parse_sbs_line(line) is None


# Lines with altitude — should be parsed correctly
def test_type7_negative_altitude():
    line = "MSG,7,1,1,484763,1,2026/05/09,07:07:31.188,2026/05/09,07:07:31.211,,-200,,,,,,,,,,"
    result = _parse_sbs_line(line)
    assert result is not None
    assert result["hex"] == "484763"
    assert result["altitude"] == -200


def test_type5_positive_altitude():
    line = "MSG,5,1,1,484763,1,2026/05/09,07:13:05.322,2026/05/09,07:13:05.335,,200,,,,,,,0,,0,"
    result = _parse_sbs_line(line)
    assert result is not None
    assert result["hex"] == "484763"
    assert result["altitude"] == 200
