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

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "flighttracker.db"))
STALE_SECONDS = 120
MAX_POINTS = 3000

# IPs allowed to call /sbs (feeder) and /db (backup download)
ALLOWED_IPS = {
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
}

SBS_IDX = {
    "msg_type":  1,
    "hex":       4,
    "altitude": 11,
    "lat":      14,
    "lon":      15,
    "on_ground":21,
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

_last_seen: dict[str, datetime] = {}              # hex -> last seen (UTC)
_last_alt:  dict[str, tuple[datetime, int]] = {}  # hex -> (ts, altitude_ft)
_lock = threading.Lock()

MAX_CLIMB_RATE = 1000  # ft/s — readings exceeding this are dropped as outliers


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

            CREATE TABLE IF NOT EXISTS agl_offsets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                icao_hex      TEXT    NOT NULL,
                session_start TEXT    NOT NULL,
                offset_ft     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_offsets ON agl_offsets(icao_hex, session_start);
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

    if msg_type in ("2", "3", "5"):
        result["altitude"] = get(SBS_IDX["altitude"], int)
    if msg_type in ("2", "3"):
        result["lat"] = get(SBS_IDX["lat"], float)
        result["lon"] = get(SBS_IDX["lon"], float)

    if len(parts) > SBS_IDX["on_ground"]:
        gnd = get(SBS_IDX["on_ground"])
        if gnd is not None:
            result["on_ground"] = 1 if gnd in ("1", "-1") else 0

    return result


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _ingest(messages: list[str]) -> None:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    db_rows: list[tuple] = []

    with _lock:
        for line in messages:
            parsed = _parse_sbs_line(line)
            if not parsed:
                continue

            altitude = parsed.get("altitude")
            lat = parsed.get("lat")
            lon = parsed.get("lon")
            if altitude is None and (lat is None or lon is None):
                continue

            hex_code = parsed["hex"]

            if altitude is not None:
                prev = _last_alt.get(hex_code)
                new_session = prev is None or (now - prev[0]).total_seconds() > STALE_SECONDS

                # Outlier filter: drop readings that imply an impossible climb/descent rate
                if not new_session:
                    prev_ts, prev_alt = prev
                    dt = (now - prev_ts).total_seconds()
                    if dt >= 1 and abs(altitude - prev_alt) / dt > MAX_CLIMB_RATE:
                        continue

                _last_alt[hex_code] = (now, altitude)

                if new_session and -500 <= altitude <= 500:
                    db_rows.append(("offset", hex_code, now_iso, altitude))

            _last_seen[hex_code] = now
            db_rows.append((
                "reading", hex_code, now_iso, altitude,
                lat, lon, parsed.get("on_ground"),
            ))

    if not db_rows:
        return

    with _db() as conn:
        for row in db_rows:
            if row[0] == "offset":
                conn.execute(
                    "INSERT INTO agl_offsets (icao_hex, session_start, offset_ft) VALUES (?,?,?)",
                    row[1:],
                )
            else:
                conn.execute(
                    "INSERT INTO readings (icao_hex, ts, alt_baro, lat, lon, on_ground)"
                    " VALUES (?,?,?,?,?,?)",
                    row[1:],
                )


# ---------------------------------------------------------------------------
# Rate of climb
# ---------------------------------------------------------------------------

def _compute_roc(rows, window_secs: int = 60) -> list[int]:
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
    if request.remote_addr not in ALLOWED_IPS:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    _ingest(messages)
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
    with _db() as conn:
        rows = conn.execute(
            "SELECT icao_hex, MAX(ts) AS last_seen FROM readings GROUP BY icao_hex"
        ).fetchall()
    seen = {r["icao_hex"]: r["last_seen"] for r in rows}
    return jsonify({h: seen.get(h) for h in TARGET_AIRCRAFT})


@app.route("/api/altitude/<icao_hex>")
def altitude(icao_hex: str):
    minutes = request.args.get("minutes", 30, type=int)
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    with _db() as conn:
        rows = conn.execute(
            """
            SELECT r.ts, r.alt_baro,
                   COALESCE(
                       (SELECT o.offset_ft
                          FROM agl_offsets o
                         WHERE o.icao_hex = r.icao_hex
                           AND o.session_start <= r.ts
                         ORDER BY o.session_start DESC
                         LIMIT 1),
                       0
                   ) AS offset_ft
              FROM readings r
             WHERE r.icao_hex = ?
               AND r.ts >= ?
               AND r.alt_baro IS NOT NULL
             ORDER BY r.ts
            """,
            (icao_hex, since),
        ).fetchall()

    step = max(1, len(rows) // MAX_POINTS)
    rows = rows[::step]

    roc = _compute_roc(rows)
    return jsonify({
        "session_start": _find_session_start(icao_hex),
        "points": [
            {"t": r["ts"], "baro": r["alt_baro"], "agl": r["alt_baro"] - r["offset_ft"], "roc": roc[i]}
            for i, r in enumerate(rows)
        ],
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


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    print("FlightTracker website server")
    print(f"Tracking {len(TARGET_AIRCRAFT)} aircraft")
    app.run(host="0.0.0.0", port=5000, debug=True)
