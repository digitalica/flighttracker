#!/usr/bin/env python3
"""
FlightTracker test server.

Receives SBS message batches from the feeder, parses them into per-aircraft
state, and exposes simple HTTP endpoints to inspect the data.

Endpoints
---------
POST /sbs          Ingest a batch of SBS messages from the feeder
GET  /tracked      Current state of the three target aircraft
GET  /all          Current state of all seen aircraft
"""

from flask import Flask, request, jsonify
from datetime import datetime, timezone
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
log.info("FlightTracker test server starting")

# Target aircraft: ICAO hex (lowercase) -> registration
TARGET_AIRCRAFT = {
    "484763": "PH-TGC",
    "48484c": "PH-GYS",
    "4849b9": "PH-GOZ",
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
}

# SBS field indices (0-based after splitting on comma)
# MSG,<type>,<sid>,<aid>,<hex>,<fid>,<date_gen>,<time_gen>,<date_log>,<time_log>,
#     <callsign>,<alt>,<gs>,<track>,<lat>,<lon>,<vrate>,<squawk>,<alert>,<emerg>,<spi>,<gnd>
SBS_IDX = {
    "msg_type": 1,
    "hex": 4,
    "callsign": 10,
    "altitude": 11,
    "ground_speed": 12,
    "track": 13,
    "lat": 14,
    "lon": 15,
    "vertical_rate": 16,
    "squawk": 17,
    "on_ground": 21,
}

_state: dict[str, dict] = {}  # hex -> aircraft state
_lock = threading.Lock()

STALE_SECONDS = 120  # drop aircraft not seen for this long


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sbs_line(line: str) -> dict | None:
    """Return a dict with populated fields from a single SBS MSG line, or None."""
    if not line.startswith("MSG,"):
        return None
    parts = line.split(",")
    if len(parts) < 11:
        return None

    def get(idx, cast=str):
        try:
            v = parts[idx].strip()
            return cast(v) if v != "" else None
        except (IndexError, ValueError):
            return None

    msg_type = get(SBS_IDX["msg_type"])
    hex_code = get(SBS_IDX["hex"])
    if not hex_code:
        return None
    hex_code = hex_code.lower()

    update: dict = {"hex": hex_code, "_msg_type": msg_type}

    if msg_type in ("1", "5", "6"):
        cs = get(SBS_IDX["callsign"])
        if cs:
            update["callsign"] = cs.strip()

    if msg_type in ("2", "3", "5"):
        update["altitude"] = get(SBS_IDX["altitude"], int)

    if msg_type in ("2", "3"):
        update["lat"] = get(SBS_IDX["lat"], float)
        update["lon"] = get(SBS_IDX["lon"], float)

    if msg_type in ("3", "4"):
        update["ground_speed"] = get(SBS_IDX["ground_speed"], float)
        update["track"] = get(SBS_IDX["track"], float)
        update["vertical_rate"] = get(SBS_IDX["vertical_rate"], int)

    if msg_type == "6":
        update["squawk"] = get(SBS_IDX["squawk"])

    gnd = get(SBS_IDX["on_ground"]) if len(parts) > SBS_IDX["on_ground"] else None
    if gnd is not None:
        update["on_ground"] = gnd in ("1", "-1")

    return update


def _ingest(messages: list[str]):
    now = _now_iso()
    with _lock:
        for line in messages:
            update = _parse_sbs_line(line)
            if not update:
                continue
            hex_code = update.pop("hex")
            update.pop("_msg_type", None)
            if hex_code not in _state:
                _state[hex_code] = {"hex": hex_code}
            _state[hex_code].update({k: v for k, v in update.items() if v is not None})
            _state[hex_code]["_last_seen"] = now


def _purge_stale():
    cutoff = datetime.now(timezone.utc).timestamp() - STALE_SECONDS
    with _lock:
        stale = [
            h for h, ac in _state.items()
            if datetime.fromisoformat(ac.get("_last_seen", "1970-01-01T00:00:00+00:00")).timestamp() < cutoff
        ]
        for h in stale:
            del _state[h]


_total_messages = 0


@app.route("/sbs", methods=["POST"])
def ingest():
    global _total_messages
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    _ingest(messages)
    _purge_stale()

    _total_messages += len(messages)
    with _lock:
        aircraft_count = len(_state)
        tracked_parts = []
        for hex_code, reg in TARGET_AIRCRAFT.items():
            ac = _state.get(hex_code)
            if ac:
                alt = ac.get("altitude")
                alt_str = f"{alt}ft" if alt is not None else "alt?"
                tracked_parts.append(f"{reg}:{alt_str}")

    log.info(
        f"Batch: {len(messages):4d} msgs | total: {_total_messages:6d} | "
        f"aircraft in state: {aircraft_count:3d} | "
        f"tracked: {', '.join(tracked_parts) if tracked_parts else 'none'}"
    )
    return jsonify({"ok": True, "count": len(messages)})


@app.route("/tracked")
def tracked():
    with _lock:
        snapshot = {}
        for hex_code, reg in TARGET_AIRCRAFT.items():
            ac = _state.get(hex_code)
            snapshot[reg] = {
                "seen": ac is not None,
                "altitude_ft": ac.get("altitude") if ac else None,
                "callsign": ac.get("callsign") if ac else None,
                "lat": ac.get("lat") if ac else None,
                "lon": ac.get("lon") if ac else None,
                "ground_speed": ac.get("ground_speed") if ac else None,
                "on_ground": ac.get("on_ground") if ac else None,
                "last_seen": ac.get("_last_seen") if ac else None,
            }
    return jsonify(snapshot)


@app.route("/all")
def all_aircraft():
    with _lock:
        snapshot = dict(_state)
    return jsonify(snapshot)


if __name__ == "__main__":
    print("FlightTracker test server")
    print(f"Tracking: {', '.join(f'{r} ({h})' for h, r in TARGET_AIRCRAFT.items())}")
    print("Endpoints:")
    print("  POST http://0.0.0.0:5000/sbs      <- feeder sends here")
    print("  GET  http://0.0.0.0:5000/tracked  <- your 3 aircraft")
    print("  GET  http://0.0.0.0:5000/all      <- everything")
    app.run(host="0.0.0.0", port=5000, debug=True)
