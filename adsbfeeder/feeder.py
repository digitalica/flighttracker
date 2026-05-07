#!/usr/bin/env python3
"""
ADS-B feeder client.

Reads the SBS (BaseStation) TCP stream from the local dump1090/readsb instance,
filters for target aircraft, and forwards message batches to the tracking server.
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

SERVER_URL = "https://phtgc.nl/sbs"
SEND_INTERVAL = 1        # seconds between POSTs
BATCH_MAX = 10           # max messages per batch
RECONNECT_DELAY = 10     # seconds before reconnect after disconnect

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
    "a8b0a3",  # N65909
    "3ecadc",  # D-KRUA
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
                            if line and _is_target(line):
                                _buffer.append(line)
        except (OSError, socket.timeout) as exc:
            log.warning(f"SBS connection error: {exc}")
        log.info(f"Reconnecting in {RECONNECT_DELAY}s ...")
        time.sleep(RECONNECT_DELAY)


HEARTBEAT_INTERVAL = 2   # seconds between heartbeat POSTs when buffer is empty

def send_loop():
    """Drain the buffer periodically and POST batches to the server."""
    last_send = 0.0
    while True:
        time.sleep(SEND_INTERVAL)
        with _lock:
            batch = []
            while _buffer and len(batch) < BATCH_MAX:
                batch.append(_buffer.popleft())

        now = time.monotonic()
        if not batch and (now - last_send) < HEARTBEAT_INTERVAL:
            continue

        try:
            resp = requests.post(SERVER_URL, json={"messages": batch}, timeout=10)
            resp.raise_for_status()
            last_send = time.monotonic()
            if batch:
                log.info(f"Sent {len(batch)} messages -> HTTP {resp.status_code}")
            else:
                log.info(f"Heartbeat -> HTTP {resp.status_code}")
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
    log.info(f"Tracking   : {', '.join(sorted(TARGET_HEXES))}")
    log.info(f"Send interval: {SEND_INTERVAL}s")

    reader = threading.Thread(target=read_sbs, daemon=True, name="sbs-reader")
    reader.start()

    send_loop()  # runs in main thread


if __name__ == "__main__":
    main()
