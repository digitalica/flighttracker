#!/usr/bin/env python3
"""
ADS-B feeder client.

Reads the SBS (BaseStation) TCP stream from the local dump1090/readsb instance,
filters for target aircraft, and forwards message batches to the tracking server.

When the server is unreachable, messages are persisted to a local SQLite database
and retransmitted (oldest first) once the server is reachable again. Messages older
than MAX_BACKLOG_DAYS are pruned automatically.
"""

import os
import socket
import sqlite3
import time
import logging
import sys
import threading
from collections import deque
from pathlib import Path

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

METRICS_PORT = 9877

MAX_BACKLOG_DAYS = 7
PERSIST_PATH = Path(os.environ.get("FEEDER_DB", Path(__file__).parent / "pending.db"))

# Only messages for these ICAO hex codes are forwarded to the server
TARGET_HEXES = {
    "484763",  # PH-TGC
    "48484c",  # PH-GYS
    "4849b9",  # PH-GOZ
    "4849a0",  # PH-ACX
    "484ae6",  # PH-GBA
    "4848f9",  # PH-RYF
    "484583",  # PH-RIS
    "48462c",  # PH-SKC
    "48459c",  # PH-VHA
    "4845f0",  # PH-VHD
    "484608",  # PH-JBC
    "484655",  # PH-CBN
    "48481f",  # PH-WMA
    "486237",  # PH-VHY
    "485fd8",  # PH-VHP
    "4863ff",  # PH-VHK
    "484406",  # PH-CJC
    "4869bc",  # PH-VHM
    "4845bb",  # PH-4B7
    "3e5e11",  # DK-AUZ
    "4847d7",  # PH-TGA
    "4849b7",  # PH1372
    "484f66",  # PH1489
    "484b68",  # PH1432
    "4845ae",  # PH-DON
    "484737",  # PH-LEN
    "484846",  # PH1133
    "485e08",  # PH-4T7
    "484bf9",  # PH-GIN
    "48462e",  # PH-MFT
    "48487e",  # PH-2X3
    "484d14",  # PH1466
    "484ff2",  # PH-PLP
    "a8b0a3",  # N65909
    "3ecadc",  # D-KRUA
    "484c49",  # PH1311
}


def _is_target(line: str) -> bool:
    """Return True if this SBS line belongs to one of the target aircraft."""
    parts = line.split(",")
    if len(parts) < 5:
        return False
    return parts[4].strip().lower() in TARGET_HEXES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

_buffer: deque[str] = deque()
_lock = threading.Lock()

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
        except (OSError, socket.timeout) as exc:
            log.warning(f"SBS connection error: {exc}")
        _sbs_connected.set(0)
        log.info(f"Reconnecting in {RECONNECT_DELAY}s ...")
        time.sleep(RECONNECT_DELAY)


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

    reader = threading.Thread(target=read_sbs, daemon=True, name="sbs-reader")
    reader.start()

    send_loop()  # runs in main thread


if __name__ == "__main__":
    main()
