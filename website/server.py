#!/usr/bin/env python3
"""
FlightTracker website server.

Receives SBS batches from the feeder, stores altitude readings in SQLite,
and serves a Chart.js altitude graph.

Endpoints
---------
POST /sbs                    Ingest SBS batch from feeder
GET  /api/aircraft           List of tracked aircraft (registration + hex)
GET  /api/altitude/<hex>     Altitude time-series (?minutes=30)
GET  /                       Frontend
"""

import logging
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "flighttracker.db"))
STALE_SECONDS   = 120
INACTIVE_SECS   = 30
MAX_POINTS = 3000

# IPs allowed to call /sbs (feeder) and /db (backup download)
ALLOWED_IPS = {
    "127.0.0.1",       # localhost
    "45.83.241.206",   # desktop, public
    "100.111.194.45",  # desktop, tailscale
    "80.57.68.254",    # feeder, skydive hilversum public ip
}

TARGET_AIRCRAFT = {
    "484763": "PH-TGC",
    "48484c": "PH-GYS",
    "4849b9": "PH-GOZ",
    "4849a0": "PH-ACX",
    "484ae6": "PH-GBA",
    "4848f9": "PH-RYF",
    "484583": "PH-RIS",
    "48462c": "PH-SKC",
    "48459c": "PH-VHA",
    "4845f0": "PH-VHD",
    "484608": "PH-JBC",
    "484655": "PH-CBN",
    "48481f": "PH-WMA",
    "486237": "PH-VHY",
    "485fd8": "PH-VHP",
    "4863ff": "PH-VHK",
    "484406": "PH-CJC",
    "4869bc": "PH-VHM",
    "4845bb": "PH-4B7",
    "3e5e11": "DK-AUZ",
    "4847d7": "PH-TGA",
    "4849b7": "PH1372",
    "484f66": "PH1489",
    "484b68": "PH1432",
    "4845ae": "PH-DON",
    "484737": "PH-LEN",
    "484846": "PH1133",
    "485e08": "PH-4T7",
    "484bf9": "PH-GIN",
    "48462e": "PH-MFT",
    "48487e": "PH-2X3",
    "484d14": "PH1466",
    "484ff2": "PH-PLP",
    "a8b0a3": "N65909",
    "3ecadc": "D-KRUA",
}

SBS_IDX = {
    "msg_type":  1,
    "hex":       4,
    "date_gen":  6,
    "time_gen":  7,
    "altitude": 11,
    "lat":      14,
    "lon":      15,
    "on_ground":21,
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

_last_seen: dict[str, datetime] = {}              # hex -> last seen (UTC)
_last_alt:  dict[str, tuple[datetime, int]] = {}  # hex -> (ts, altitude_ft)
_last_post: datetime | None = None               # last POST /sbs received (UTC)
_visitors:  dict[str, datetime] = {}             # ip -> last seen (UTC)
_lock = threading.Lock()

VISITOR_TIMEOUT = 60  # seconds before a visitor is considered inactive

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_prom_messages_received = Counter(
    "flighttracker_messages_received_total",
    "SBS messages received in POST /sbs batches",
)
_prom_messages_parsed = Counter(
    "flighttracker_messages_parsed_total",
    "SBS messages successfully parsed and stored",
)
_prom_message_lag = Histogram(
    "flighttracker_message_lag_seconds",
    "Seconds between message timestamp and server receipt time",
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600],
)
_prom_api_requests = Counter(
    "flighttracker_api_requests_total",
    "HTTP requests per endpoint",
    ["endpoint"],
)

# Pre-initialise all known endpoints so they appear in /metrics from startup
for _ep in ("ingest", "list_aircraft", "status", "altitude", "events",
            "current_altitude", "download_db", "index",
            "robots", "sitemap", "metrics"):
    _prom_api_requests.labels(endpoint=_ep)


@app.after_request
def _count_request(response):
    if request.endpoint and request.endpoint != "metrics":
        _prom_api_requests.labels(endpoint=request.endpoint).inc()
    return response



# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    with _db() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS readings (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                icao_hex  TEXT    NOT NULL,
                ts        TEXT    NOT NULL,
                alt_baro  INTEGER,
                lat       REAL,
                lon       REAL,
                on_ground INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_readings ON readings(icao_hex, ts);
        """)


_init_db()


# ---------------------------------------------------------------------------
# SBS parsing
# ---------------------------------------------------------------------------

def _parse_sbs_line(line: str) -> dict | None:
    if not line.startswith("MSG,"):
        return None
    parts = line.split(",")
    if len(parts) < 11:
        return None

    def get(idx, cast=str):
        try:
            v = parts[idx].strip()
            return cast(v) if v else None
        except (IndexError, ValueError):
            return None

    msg_type = get(SBS_IDX["msg_type"])
    hex_code = get(SBS_IDX["hex"])
    if not hex_code:
        return None

    result: dict = {"hex": hex_code.lower(), "msg_type": msg_type}

    date_str = get(SBS_IDX["date_gen"])
    time_str = get(SBS_IDX["time_gen"])
    if date_str and time_str:
        for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
            try:
                result["ts"] = datetime.strptime(
                    f"{date_str} {time_str}", fmt
                ).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                pass

    if msg_type in ("2", "3", "5", "7"):
        result["altitude"] = get(SBS_IDX["altitude"], int)
    if msg_type in ("2", "3"):
        result["lat"] = get(SBS_IDX["lat"], float)
        result["lon"] = get(SBS_IDX["lon"], float)

    if len(parts) > SBS_IDX["on_ground"]:
        gnd = get(SBS_IDX["on_ground"])
        if gnd is not None:
            result["on_ground"] = 1 if gnd in ("1", "-1") else 0

    if result.get("altitude") is None and (result.get("lat") is None or result.get("lon") is None):
        return None

    return result


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _ingest(messages: list[str]) -> tuple[int, int, list[float]]:
    now = datetime.now(timezone.utc)
    db_rows: list[tuple] = []
    parsed_count = 0
    seen_aircraft: set[str] = set()
    lags: list[float] = []

    with _lock:
        for line in messages:
            parsed = _parse_sbs_line(line)
            if not parsed:
                continue

            parsed_count += 1
            hex_code = parsed["hex"]
            seen_aircraft.add(hex_code)
            msg_ts = parsed.get("ts") or now
            altitude = parsed.get("altitude")

            lags.append((now - msg_ts).total_seconds())

            if altitude is not None:
                _last_alt[hex_code] = (msg_ts, altitude)

            _last_seen[hex_code] = msg_ts
            db_rows.append((
                hex_code, msg_ts.isoformat(), altitude,
                parsed.get("lat"), parsed.get("lon"), parsed.get("on_ground"),
            ))

    if not db_rows:
        return 0, 0, []

    with _db() as conn:
        for row in db_rows:
            conn.execute(
                "INSERT INTO readings (icao_hex, ts, alt_baro, lat, lon, on_ground)"
                " VALUES (?,?,?,?,?,?)",
                row,
            )

    return parsed_count, len(seen_aircraft), lags


# ---------------------------------------------------------------------------
# Rate of climb
# ---------------------------------------------------------------------------

def _compute_roc(rows, window_secs: int = 15) -> list[int]:
    """Smoothed rate of climb in ft/min using a sliding time window."""
    n = len(rows)
    if n < 2:
        return [0] * n
    times = [datetime.fromisoformat(r["ts"]).timestamp() for r in rows]
    alts  = [r["alt_baro"] for r in rows]
    result = []
    for i in range(n):
        t  = times[i]
        lo, hi = i, i
        while lo > 0     and times[lo - 1] >= t - window_secs: lo -= 1
        while hi < n - 1 and times[hi + 1] <= t + window_secs: hi += 1
        dt = times[hi] - times[lo]
        result.append(round((alts[hi] - alts[lo]) / dt * 60) if dt > 0 else 0)
    return result


def _filter_altitude_outliers(rows, max_rate_ft_per_min: int = 5000) -> list:
    """Drop rows where altitude implies an impossible rate vs all available neighbors.

    A point is an outlier only when every nearby neighbor exceeds the threshold,
    so a single spike is removed without also flagging the points on either side.
    Neighbors more than STALE_SECONDS apart are ignored — they belong to a
    different session and would otherwise produce an artificially low rate that
    masks the spike.
    """
    if len(rows) < 2:
        return list(rows)
    times = [datetime.fromisoformat(r["ts"]).timestamp() for r in rows]
    alts  = [r["alt_baro"] for r in rows]
    n = len(rows)
    keep = [True] * n
    for i in range(n):
        rates = []
        for j in (i - 1, i + 1):
            if j < 0 or j >= n:
                continue
            dt = abs(times[i] - times[j])
            if 0 < dt <= STALE_SECONDS:
                rates.append(abs(alts[i] - alts[j]) / dt * 60)
        if rates and all(r > max_rate_ft_per_min for r in rates):
            keep[i] = False
    return [r for r, k in zip(rows, keep) if k]


def _compute_agl_offset(rows) -> int:
    """Compute ground offset in ft by averaging all rows at the two lowest altitude values,
    rounded to the nearest 100 ft. Only altitudes below 1000 ft are considered."""
    candidates = [r["alt_baro"] for r in rows if r["alt_baro"] < 1000]
    if not candidates:
        return 0
    two_lowest = sorted(set(candidates))[:2]
    matching = [a for a in candidates if a in two_lowest]
    return round(sum(matching) / len(matching) / 100) * 100


def _detect_events(rows, agl_offset: int) -> list[dict]:
    """Detect altitude threshold crossings from a sequence of readings.

    Each threshold has separate up/down trigger levels (hysteresis) to prevent
    noise around the boundary from firing multiple events. State resets to
    unknown after any gap > STALE_SECONDS.
    """
    # (trig_up, trig_down, event_up, event_down)
    THRESHOLDS = [
        (300,  100,  "takeoff",        "landing"),
        (3100, 2900, "climbing_3000",  "descending_3000"),
        (5600, 5400, "climbing_5500",  "descending_5500"),
    ]
    events = []
    states = [None] * len(THRESHOLDS)  # None=unknown, True=above, False=below
    descent_fired = False  # suppress extra descending events once one has fired
    if rows:
        events.append({"type": "active", "ts": rows[0]["ts"]})
    for i, row in enumerate(rows):
        if i > 0:
            dt = (datetime.fromisoformat(row["ts"]) - datetime.fromisoformat(rows[i - 1]["ts"])).total_seconds()
            if dt > INACTIVE_SECS:
                events.append({"type": "inactive", "ts": rows[i - 1]["ts"]})
                events.append({"type": "active",   "ts": row["ts"]})
            if dt > STALE_SECONDS:
                states = [None] * len(THRESHOLDS)
                descent_fired = False
        agl = row["alt_baro"] - agl_offset
        for j, (trig_up, trig_down, up_name, down_name) in enumerate(THRESHOLDS):
            s = states[j]
            if s is None:
                if agl > trig_up:     states[j] = True
                elif agl < trig_down: states[j] = False
            elif s and agl < trig_down:
                is_altitude_descent = down_name != "landing"
                if not (is_altitude_descent and descent_fired):
                    events.append({"type": down_name, "ts": row["ts"]})
                if is_altitude_descent:
                    descent_fired = True
                states[j] = False
            elif not s and agl > trig_up:
                events.append({"type": up_name, "ts": row["ts"]})
                if up_name == "takeoff":
                    descent_fired = False
                states[j] = True
    if rows:
        last_dt = (datetime.now(timezone.utc) - datetime.fromisoformat(rows[-1]["ts"])).total_seconds()
        if last_dt > INACTIVE_SECS:
            events.append({"type": "inactive", "ts": rows[-1]["ts"]})

    # Merge landing + takeoff within 90 s into a single touch_and_go
    TOUCH_AND_GO_SECS = 90
    merged = []
    i = 0
    while i < len(events):
        ev = events[i]
        if (ev["type"] == "landing"
                and i + 1 < len(events)
                and events[i + 1]["type"] == "takeoff"):
            gap = (datetime.fromisoformat(events[i + 1]["ts"])
                   - datetime.fromisoformat(ev["ts"])).total_seconds()
            if gap <= TOUCH_AND_GO_SECS:
                merged.append({"type": "touch_and_go", "ts": events[i + 1]["ts"]})
                i += 2
                continue
        merged.append(ev)
        i += 1
    return merged


def _find_session_start(icao_hex: str) -> str | None:
    """Return the timestamp of the first reading in the current flight session."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _db() as conn:
        row = conn.execute(
            """
            WITH ordered AS (
              SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
              FROM readings
              WHERE icao_hex = ? AND ts >= ?
            ),
            starts AS (
              SELECT ts FROM ordered
              WHERE prev_ts IS NULL
                 OR (julianday(ts) - julianday(prev_ts)) * 86400 > ?
            )
            SELECT MAX(ts) AS session_start FROM starts
            """,
            (icao_hex, since, STALE_SECONDS),
        ).fetchone()
    return row["session_start"] if row else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/sbs", methods=["POST"])
def ingest():
    global _last_post
    if request.remote_addr not in ALLOWED_IPS:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    with _lock:
        _last_post = datetime.now(timezone.utc)
    parsed, aircraft, lags = _ingest(messages)
    _prom_messages_received.inc(len(messages))
    _prom_messages_parsed.inc(parsed)
    for lag in lags:
        _prom_message_lag.observe(lag)
    if lags:
        lag_info = f"  lag min/avg/max: {min(lags):.1f}/{sum(lags)/len(lags):.1f}/{max(lags):.1f}s"
    else:
        lag_info = ""
    log.info(f"ingest: {len(messages)} received, {parsed} parsed, {aircraft} aircraft{lag_info}")
    return jsonify({"ok": True, "count": len(messages)})


@app.route("/api/aircraft")
def list_aircraft():
    result = sorted(
        [{"hex": h, "registration": r} for h, r in TARGET_AIRCRAFT.items()],
        key=lambda x: x["registration"],
    )
    return jsonify(result)


@app.route("/api/status")
def status():
    now = datetime.now(timezone.utc)
    with _lock:
        _visitors[request.remote_addr] = now
        cutoff = now.timestamp() - VISITOR_TIMEOUT
        active = sum(1 for t in _visitors.values() if t.timestamp() >= cutoff)
        last_post = _last_post.isoformat() if _last_post else None
    with _db() as conn:
        rows = conn.execute(
            "SELECT icao_hex, MAX(ts) AS last_seen FROM readings GROUP BY icao_hex"
        ).fetchall()
    seen = {r["icao_hex"]: r["last_seen"] for r in rows}
    return jsonify({
        "last_post": last_post,
        "active_users": active,
        "aircraft": {h: seen.get(h) for h in TARGET_AIRCRAFT},
    })


@app.route("/api/altitude/<icao_hex>")
def altitude(icao_hex: str):
    minutes = request.args.get("minutes", 30, type=int)

    if "fake" in request.args:
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=minutes)
        fake_rows = []
        t = start
        while t <= now:
            alt = int(t.second / 59 * 6000)
            fake_rows.append({"t": t.isoformat(), "baro": alt, "agl": alt, "roc": 0})
            t += timedelta(seconds=10)
        return jsonify({"session_start": start.isoformat(), "points": fake_rows})

    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    with _db() as conn:
        rows = conn.execute(
            """
            SELECT ts, alt_baro
              FROM readings
             WHERE icao_hex = ?
               AND ts >= ?
               AND alt_baro IS NOT NULL
             ORDER BY ts
            """,
            (icao_hex, since),
        ).fetchall()

    rows = _filter_altitude_outliers(rows)
    agl_offset = _compute_agl_offset(rows)

    step = max(1, len(rows) // MAX_POINTS)
    rows = rows[::step]

    roc = _compute_roc(rows)
    return jsonify({
        "session_start": _find_session_start(icao_hex),
        "points": [
            {"t": r["ts"], "baro": r["alt_baro"], "agl": r["alt_baro"] - agl_offset, "roc": roc[i]}
            for i, r in enumerate(rows)
        ],
    })


@app.route("/api/events/<icao_hex>")
def events(icao_hex: str):
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    with _db() as conn:
        rows = conn.execute(
            """
            SELECT ts, alt_baro
              FROM readings
             WHERE icao_hex = ?
               AND ts >= ?
               AND alt_baro IS NOT NULL
             ORDER BY ts
            """,
            (icao_hex, since),
        ).fetchall()

    rows = _filter_altitude_outliers(rows)
    agl_offset = _compute_agl_offset(rows)
    return jsonify({
        "agl_offset": agl_offset,
        "events": _detect_events(rows, agl_offset),
    })


@app.route("/api/current")
def current_altitude():
    """Return the current AGL altitude for one aircraft.

    Query params:
      ac      — registration or ICAO hex (default: PH-TGC)
      simple  — if set, return plain-text altitude only (null if unavailable or
                stale > 60 s), e.g. /api/current?simple=1
    """
    ac = request.args.get("ac", "PH-TGC")
    # Accept both registration and hex
    icao_hex = next(
        (h for h, r in TARGET_AIRCRAFT.items() if r.upper() == ac.upper()),
        ac.lower() if ac.lower() in TARGET_AIRCRAFT else "484763",
    )
    registration = TARGET_AIRCRAFT.get(icao_hex, icao_hex)
    simple = "simple" in request.args

    if "fake" in request.args:
        now  = datetime.now(timezone.utc)
        agl  = int(now.second / 59 * 6000)
        ts   = now.isoformat()
        if simple:
            return str(agl) + "\n", 200, {"Content-Type": "text/plain"}
        return jsonify({"registration": registration, "hex": icao_hex,
                        "agl": agl, "baro": agl, "agl_offset": 0,
                        "ts": ts, "age_secs": 0})

    since_today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    with _db() as conn:
        rows = conn.execute(
            """
            SELECT ts, alt_baro FROM readings
             WHERE icao_hex = ? AND ts >= ? AND alt_baro IS NOT NULL
             ORDER BY ts
            """,
            (icao_hex, since_today),
        ).fetchall()

    if not rows:
        return ("null\n", 200, {"Content-Type": "text/plain"}) if simple else \
               jsonify({"registration": registration, "hex": icao_hex,
                        "agl": None, "baro": None, "ts": None, "age_secs": None})

    rows = _filter_altitude_outliers(rows)
    agl_offset = _compute_agl_offset(rows)
    last = rows[-1]
    baro = last["alt_baro"]
    ts   = last["ts"]
    age  = round((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds())
    agl  = baro - agl_offset

    if simple:
        value = "null" if age > 60 else str(agl)
        return value + "\n", 200, {"Content-Type": "text/plain"}

    return jsonify({
        "registration": registration,
        "hex":          icao_hex,
        "agl":          agl,
        "baro":         baro,
        "agl_offset":   agl_offset,
        "ts":           ts,
        "age_secs":     age,
    })


@app.route("/db")
def download_db():
    if request.remote_addr not in ALLOWED_IPS:
        return jsonify({"error": "forbidden"}), 403
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp.name)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return send_file(
        tmp.name,
        as_attachment=True,
        download_name="flighttracker.db",
        mimetype="application/octet-stream",
    )


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/robots.txt")
def robots():
    return (
        "User-agent: *\nAllow: /\nSitemap: https://phtgc.nl/sitemap.xml\n",
        200,
        {"Content-Type": "text/plain"},
    )


@app.route("/sitemap.xml")
def sitemap():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        "    <loc>https://phtgc.nl/</loc>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return xml, 200, {"Content-Type": "application/xml"}


if __name__ == "__main__":
    print("FlightTracker website server")
    print(f"Tracking {len(TARGET_AIRCRAFT)} aircraft")
    app.run(host="0.0.0.0", port=5000, debug=True)
