#!/usr/bin/env python3
"""
ADS-B feeder client.

Reads the SBS (BaseStation) TCP stream from the local dump1090/readsb instance,
filters for target aircraft, and forwards message batches to the tracking server.

When the server is unreachable, messages are persisted to a local SQLite database
and retransmitted (oldest first) once the server is reachable again. Messages older
than MAX_BACKLOG_DAYS are pruned automatically.
"""

import json
import os
import socket
import sqlite3
import subprocess
import time
import logging
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from prometheus_client import Counter, Gauge, start_http_server

SBS_HOST = "localhost"
SBS_PORT = 30003

SERVER_URL = "https://phtgc.nl/sbs"
SEND_INTERVAL = 1        # seconds between POSTs
BATCH_MAX = 100          # max messages per backlog catch-up batch
RECONNECT_DELAY = 10     # seconds before reconnect after disconnect
HEARTBEAT_INTERVAL = 2   # seconds between heartbeat POSTs when buffer is empty
CATCHUP_FACTOR = 10      # backlog batches are this many times larger than normal

STALE_SECONDS = 120      # neighbor gap beyond which altitude outlier filtering ignores a point

ALSA_DEVICE           = "plughw:Device"  # USB PnP Sound Device (aplay -l to verify)

TGC_HEX               = "484763"
ANNOUNCE_POLL_URL     = SERVER_URL.replace("/sbs", "/api/feeder/poll")
ANNOUNCE_REPORT_URL   = SERVER_URL.replace("/sbs", "/api/feeder/announced")
ANNOUNCE_INTERVAL_ACTIVE = 1   # seconds when PH-TGC is active
ANNOUNCE_INTERVAL_IDLE   = 5   # seconds otherwise
TGC_ACTIVE_SECS          = 120 # consider active if seen within this window

def _speech_texts(reg: str) -> dict[str, str]:
    """Build event speech strings for a given registration (e.g. 'PH-TGC')."""
    id_ = reg.replace("-", " ")  # espeak reads each letter: "PH TGC" -> "P H T G C"
    return {
        "takeoff":         f"{id_}, takeoff",
        "landing":         f"{id_}, landing",
        "touch_and_go":    f"{id_}, touch and go",
        "climbing_3000":   f"{id_}, approaching 3500 feet",
        "climbing_5500":   f"{id_}, approaching 6000 feet",
        "descending_3000": f"{id_}, descending through 3000 feet",
        "descending_5500": f"{id_}, descending through 5500 feet",
    }

METRICS_PORT = 9877
ALTITUDE_API_PORT = 9878  # local /api/current endpoint for a LAN display device

MAX_BACKLOG_DAYS = 7
PERSIST_PATH = Path(os.environ.get("FEEDER_DB", Path(__file__).parent / "pending.db"))

# Tracked aircraft: ICAO hex -> registration. Only messages for these hexes are
# forwarded to the server. Kept in sync manually with website/server.py's
# TARGET_AIRCRAFT (duplicated intentionally rather than shared between the two).
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
    "484c49": "PH1311",
    "a0796c": "N13FY",
}
TARGET_HEXES = set(TARGET_AIRCRAFT)


def _is_target(line: str) -> bool:
    """Return True if this SBS line belongs to one of the target aircraft."""
    parts = line.split(",")
    if len(parts) < 5:
        return False
    return parts[4].strip().lower() in TARGET_HEXES


SBS_IDX = {
    "msg_type": 1,
    "hex":      4,
    "date_gen": 6,
    "time_gen": 7,
    "altitude": 11,
}


def _parse_altitude(line: str) -> dict | None:
    """Extract hex, UTC timestamp and altitude from an SBS line, if present."""
    parts = line.split(",")
    if len(parts) <= SBS_IDX["altitude"]:
        return None

    def get(idx):
        v = parts[idx].strip()
        return v if v else None

    msg_type = get(SBS_IDX["msg_type"])
    if msg_type not in ("2", "3", "5", "7"):
        return None

    hex_code = get(SBS_IDX["hex"])
    altitude = get(SBS_IDX["altitude"])
    if not hex_code or altitude is None:
        return None
    try:
        altitude = int(altitude)
    except ValueError:
        return None

    date_str = get(SBS_IDX["date_gen"])
    time_str = get(SBS_IDX["time_gen"])
    if not date_str or not time_str:
        return None
    ts = None
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            ts = datetime.strptime(f"{date_str} {time_str}", fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            pass
    if ts is None:
        return None

    return {"hex": hex_code.lower(), "ts": ts.isoformat(), "altitude": altitude}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

_buffer: deque[str] = deque()
_lock = threading.Lock()
_aircraft_last_seen: dict[str, float] = {}  # hex -> epoch time of last SBS message
_altitude_history: dict[str, deque[dict]] = {}  # hex -> deque of {"ts", "alt_baro"}, oldest first

_sbs_connected   = Gauge('feeder_sbs_connected',        '1 if currently connected to the SBS stream')
_buffer_size     = Gauge('feeder_buffer_size',           'In-memory message buffer size')
_backlog_size    = Gauge('feeder_backlog_size',          'Messages pending in SQLite backlog')
_messages_read   = Counter('feeder_messages_read_total', 'SBS messages read from stream (all aircraft)')
_target_messages = Counter('feeder_target_messages_total', 'Target aircraft messages added to buffer')
_messages_sent   = Counter('feeder_messages_sent_total', 'Messages successfully sent to server')
_send_failures   = Counter('feeder_send_failures_total', 'Failed POST attempts to server')


# ---------------------------------------------------------------------------
# Persistent backlog (SQLite)
# ---------------------------------------------------------------------------

def _init_backlog() -> None:
    with sqlite3.connect(PERSIST_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pending (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                received REAL    NOT NULL,
                msg      TEXT    NOT NULL
            )
        """)
    _prune_backlog()


def _prune_backlog() -> None:
    cutoff = time.time() - MAX_BACKLOG_DAYS * 86400
    with sqlite3.connect(PERSIST_PATH) as con:
        n = con.execute("DELETE FROM pending WHERE received < ?", (cutoff,)).rowcount
    if n:
        log.info(f"Pruned {n} messages older than {MAX_BACKLOG_DAYS} days from backlog")


def _enqueue_backlog(messages: list[str]) -> None:
    now = time.time()
    with sqlite3.connect(PERSIST_PATH) as con:
        con.executemany(
            "INSERT INTO pending(received, msg) VALUES (?, ?)",
            [(now, m) for m in messages],
        )
    log.info(f"Persisted {len(messages)} messages to backlog ({PERSIST_PATH})")


def _peek_backlog(limit: int) -> tuple[list[int], list[str]]:
    with sqlite3.connect(PERSIST_PATH) as con:
        rows = con.execute(
            "SELECT id, msg FROM pending ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def _ack_backlog(ids: list[int]) -> None:
    with sqlite3.connect(PERSIST_PATH) as con:
        con.execute(
            f"DELETE FROM pending WHERE id IN ({','.join(['?'] * len(ids))})", ids
        )


def _backlog_size() -> int:
    with sqlite3.connect(PERSIST_PATH) as con:
        return con.execute("SELECT COUNT(*) FROM pending").fetchone()[0]


# ---------------------------------------------------------------------------
# SBS reader
# ---------------------------------------------------------------------------

def _store_altitude(hex_code: str, ts: str, altitude: int) -> None:
    """Append a reading to the in-memory history and drop anything before today (UTC).

    Caller must hold _lock. Mirrors the website's since-midnight windowing so
    _compute_agl_offset() sees the same data it would from the SQLite-backed API.
    """
    hist = _altitude_history.setdefault(hex_code, deque())
    hist.append({"ts": ts, "alt_baro": altitude})
    since_today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    while hist and hist[0]["ts"] < since_today:
        hist.popleft()


def read_sbs():
    """Connect to the SBS stream and push lines into the shared buffer."""
    while True:
        try:
            log.info(f"Connecting to SBS stream at {SBS_HOST}:{SBS_PORT}")
            with socket.create_connection((SBS_HOST, SBS_PORT), timeout=30) as sock:
                log.info("Connected to SBS stream")
                _sbs_connected.set(1)
                buf = ""
                while True:
                    chunk = sock.recv(4096).decode("ascii", errors="replace")
                    if not chunk:
                        log.warning("SBS stream closed by remote")
                        break
                    buf += chunk
                    lines = buf.split("\n")
                    buf = lines.pop()  # incomplete last line
                    with _lock:
                        for line in lines:
                            line = line.strip()
                            if not line:
                                continue
                            _messages_read.inc()
                            if _is_target(line):
                                _buffer.append(line)
                                _target_messages.inc()
                                parts_hex = line.split(",")
                                if len(parts_hex) >= 5:
                                    _aircraft_last_seen[parts_hex[4].strip().lower()] = time.time()
                                parsed = _parse_altitude(line)
                                if parsed:
                                    _store_altitude(parsed["hex"], parsed["ts"], parsed["altitude"])
        except (OSError, socket.timeout) as exc:
            log.warning(f"SBS connection error: {exc}")
        _sbs_connected.set(0)
        log.info(f"Reconnecting in {RECONNECT_DELAY}s ...")
        time.sleep(RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# Altitude API
#
# Serves the same shape as website/server.py's /api/current, but sourced from
# the in-memory _altitude_history built from the live SBS stream instead of
# the website's SQLite DB. Lets a local display device on the LAN query the
# feeder directly. Logic duplicated intentionally from website/server.py.
# ---------------------------------------------------------------------------

def _filter_altitude_outliers(rows: list[dict], max_rate_ft_per_min: int = 5000) -> list[dict]:
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


def _compute_agl_offset(rows: list[dict]) -> int:
    """Compute ground offset in ft by averaging all rows at the two lowest altitude values,
    rounded to the nearest 100 ft. Only altitudes below 1000 ft are considered."""
    candidates = [r["alt_baro"] for r in rows if r["alt_baro"] < 1000]
    if not candidates:
        return 0
    two_lowest = sorted(set(candidates))[:2]
    matching = [a for a in candidates if a in two_lowest]
    return round(sum(matching) / len(matching) / 100) * 100


def _current_altitude_payload(ac: str, fake: bool) -> dict:
    """Build the /api/current response body for one aircraft."""
    icao_hex = next(
        (h for h, r in TARGET_AIRCRAFT.items() if r.upper() == ac.upper()),
        ac.lower() if ac.lower() in TARGET_AIRCRAFT else "484763",
    )
    registration = TARGET_AIRCRAFT.get(icao_hex, icao_hex)

    if fake:
        now = datetime.now(timezone.utc)
        if now.minute % 2 == 1:
            return {"registration": registration, "hex": icao_hex,
                    "agl": None, "baro": None, "ts": None, "age_secs": None}
        agl = int(now.second / 59 * 6000)
        return {"registration": registration, "hex": icao_hex,
                "agl": agl, "baro": agl, "agl_offset": 0,
                "ts": now.isoformat(), "age_secs": 0}

    since_today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    with _lock:
        rows = [r for r in _altitude_history.get(icao_hex, ()) if r["ts"] >= since_today]

    if not rows:
        return {"registration": registration, "hex": icao_hex,
                "agl": None, "baro": None, "ts": None, "age_secs": None}

    rows = _filter_altitude_outliers(rows)
    agl_offset = _compute_agl_offset(rows)
    last = rows[-1]
    baro = last["alt_baro"]
    ts   = last["ts"]
    age  = round((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds())
    agl  = baro - agl_offset

    return {"registration": registration, "hex": icao_hex,
            "agl": agl, "baro": baro, "agl_offset": agl_offset,
            "ts": ts, "age_secs": age}


class AltitudeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug("altitude-api: " + fmt, *args)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/api/current":
            self.send_response(404)
            self.end_headers()
            return

        qs = parse_qs(parsed.query, keep_blank_values=True)
        ac = qs.get("ac", ["PH-TGC"])[0]
        simple = "simple" in qs
        fake = "fake" in qs

        payload = _current_altitude_payload(ac, fake)

        if simple:
            age = payload["age_secs"]
            value = "null" if age is None or age > 60 else str(payload["agl"])
            body = (value + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Send loop
# ---------------------------------------------------------------------------

def send_loop():
    """Drain the buffer periodically and POST batches to the server.

    When a backlog exists in SQLite, drains it first (oldest messages first)
    before sending fresh messages. Fresh messages received during a backlog
    drain are persisted to SQLite so they survive a restart and maintain order.
    """
    _init_backlog()
    last_send = 0.0
    last_prune = time.monotonic()

    while True:
        time.sleep(SEND_INTERVAL)

        # Prune once a day
        if time.monotonic() - last_prune > 86400:
            _prune_backlog()
            last_prune = time.monotonic()

        # Drain all fresh messages from the in-memory buffer
        with _lock:
            batch = list(_buffer)
            _buffer.clear()
            _buffer_size.set(0)

        ids, backlog_msgs = _peek_backlog(BATCH_MAX * CATCHUP_FACTOR)

        if ids:
            # Backlog exists: combine with fresh messages in one POST so that
            # a small backlog clears in a single round-trip.
            combined = backlog_msgs + batch
            try:
                resp = requests.post(SERVER_URL, json={"messages": combined}, timeout=10)
                resp.raise_for_status()
                _ack_backlog(ids)
                _messages_sent.inc(len(combined))
                last_send = time.monotonic()
                remaining = _backlog_size()
                _backlog_size.set(remaining)
                log.info(
                    f"Backlog: sent {len(backlog_msgs)} + {len(batch)} fresh"
                    f" -> HTTP {resp.status_code}"
                    + (f" ({remaining} remaining)" if remaining else " (backlog cleared)")
                )
            except requests.exceptions.RequestException as exc:
                log.warning(f"Backlog send failed (server still unreachable): {exc}")
                _send_failures.inc()
                if batch:
                    _enqueue_backlog(batch)
            continue

        # No backlog — send fresh messages normally
        now = time.monotonic()
        if not batch and (now - last_send) < HEARTBEAT_INTERVAL:
            continue

        try:
            resp = requests.post(SERVER_URL, json={"messages": batch}, timeout=10)
            resp.raise_for_status()
            _messages_sent.inc(len(batch))
            last_send = time.monotonic()
            if batch:
                log.info(f"Sent {len(batch)} messages -> HTTP {resp.status_code}")
            else:
                log.info(f"Heartbeat -> HTTP {resp.status_code}")
        except requests.exceptions.RequestException as exc:
            log.warning(f"Failed to send batch: {exc}")
            _send_failures.inc()
            if batch:
                _enqueue_backlog(batch)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

def _speak(text: str) -> None:
    """Synthesise speech via espeak-ng piped to aplay (avoids audio device init errors)."""
    try:
        espeak = subprocess.Popen(
            ["espeak-ng", "-s", "130", "--stdout", text],
            stdout=subprocess.PIPE,
        )
        subprocess.run(["aplay", "-q", "-D", ALSA_DEVICE], stdin=espeak.stdout, timeout=10, check=False)
        espeak.wait()
    except Exception as exc:
        log.warning(f"speak failed: {exc}")


def announce_loop():
    """Poll server for events on the followed aircraft and speak new ones via espeak."""
    announced:    set[str]       = set()
    follow_hex:   str            = TGC_HEX
    speech_texts: dict[str, str] = _speech_texts("PH-TGC")

    while True:
        last_seen = _aircraft_last_seen.get(follow_hex, 0)
        active   = last_seen and (time.time() - last_seen) < TGC_ACTIVE_SECS
        interval = ANNOUNCE_INTERVAL_ACTIVE if active else ANNOUNCE_INTERVAL_IDLE

        try:
            resp = requests.get(ANNOUNCE_POLL_URL, timeout=5)
            resp.raise_for_status()
            data = resp.json()

            # Switch followed aircraft if server changed it
            new_hex = data.get("follow_hex", TGC_HEX)
            if new_hex != follow_hex:
                log.info(f"Switching follow target: {follow_hex} -> {new_hex}")
                follow_hex   = new_hex
                speech_texts = _speech_texts(data.get("follow_reg", new_hex))
                announced.clear()

            to_report = []

            if data.get("command") == "test_sound":
                label = "FlightTracker sound test"
                _speak(label)
                to_report.append({"type": "test_sound", "label": label})

            for ev in data.get("events", []):
                if ev["type"] in ("active", "inactive"):
                    continue
                if ev["ts"] in announced:
                    continue
                announced.add(ev["ts"])
                label = speech_texts.get(ev["type"])
                if label:
                    _speak(label)
                    to_report.append({"type": ev["type"], "ts": ev["ts"], "label": label})

            if to_report:
                try:
                    requests.post(ANNOUNCE_REPORT_URL, json={"events": to_report}, timeout=5)
                except Exception as exc:
                    log.warning(f"Failed to report announcements: {exc}")

        except Exception as exc:
            log.warning(f"Announce poll failed: {exc}")

        time.sleep(interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("ADS-B feeder starting")
    log.info(f"SBS source   : {SBS_HOST}:{SBS_PORT}")
    log.info(f"Server       : {SERVER_URL}")
    log.info(f"Backlog DB   : {PERSIST_PATH}")
    log.info(f"Tracking     : {', '.join(sorted(TARGET_HEXES))}")
    log.info(f"Send interval: {SEND_INTERVAL}s")
    start_http_server(METRICS_PORT)
    log.info(f"Metrics      : http://0.0.0.0:{METRICS_PORT}")

    altitude_server = ThreadingHTTPServer(("0.0.0.0", ALTITUDE_API_PORT), AltitudeHandler)
    log.info(f"Altitude API : http://0.0.0.0:{ALTITUDE_API_PORT}/api/current")

    reader    = threading.Thread(target=read_sbs,             daemon=True, name="sbs-reader")
    announcer = threading.Thread(target=announce_loop,         daemon=True, name="announcer")
    altitude  = threading.Thread(target=altitude_server.serve_forever, daemon=True, name="altitude-api")
    reader.start()
    announcer.start()
    altitude.start()

    send_loop()  # runs in main thread


if __name__ == "__main__":
    if "--test-sound" in sys.argv:
        print("Playing test sound via espeak...")
        _speak("FlightTracker sound test. P H T G C, takeoff.")
        print("Done.")
    else:
        main()
