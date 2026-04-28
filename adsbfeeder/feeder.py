#!/usr/bin/env python3
"""
ADS-B feeder client.

Reads the SBS (BaseStation) TCP stream from the local dump1090/readsb instance
and forwards message batches to the tracking server.
"""

import socket
import time
import logging
import sys
import threading
from collections import deque

import requests

SBS_HOST = "localhost"
SBS_PORT = 30003

SERVER_URL = "http://100.70.200.82:5000/sbs"
SEND_INTERVAL = 5        # seconds between POSTs
BATCH_MAX = 500          # max messages per batch
RECONNECT_DELAY = 10     # seconds before reconnect after disconnect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

_buffer: deque[str] = deque()
_lock = threading.Lock()


def read_sbs():
    """Connect to the SBS stream and push lines into the shared buffer."""
    while True:
        try:
            log.info(f"Connecting to SBS stream at {SBS_HOST}:{SBS_PORT}")
            with socket.create_connection((SBS_HOST, SBS_PORT), timeout=30) as sock:
                log.info("Connected to SBS stream")
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
                            if line:
                                _buffer.append(line)
        except (OSError, socket.timeout) as exc:
            log.warning(f"SBS connection error: {exc}")
        log.info(f"Reconnecting in {RECONNECT_DELAY}s ...")
        time.sleep(RECONNECT_DELAY)


def send_loop():
    """Drain the buffer periodically and POST batches to the server."""
    while True:
        time.sleep(SEND_INTERVAL)
        with _lock:
            if not _buffer:
                continue
            batch = []
            while _buffer and len(batch) < BATCH_MAX:
                batch.append(_buffer.popleft())

        try:
            resp = requests.post(SERVER_URL, json={"messages": batch}, timeout=10)
            resp.raise_for_status()
            log.info(f"Sent {len(batch)} messages -> HTTP {resp.status_code}")
        except requests.exceptions.RequestException as exc:
            log.warning(f"Failed to send batch: {exc}")
            # Put messages back so they are not lost
            with _lock:
                for msg in reversed(batch):
                    _buffer.appendleft(msg)


def main():
    log.info("ADS-B feeder starting")
    log.info(f"SBS source : {SBS_HOST}:{SBS_PORT}")
    log.info(f"Server     : {SERVER_URL}")
    log.info(f"Send interval: {SEND_INTERVAL}s")

    reader = threading.Thread(target=read_sbs, daemon=True, name="sbs-reader")
    reader.start()

    send_loop()  # runs in main thread


if __name__ == "__main__":
    main()
