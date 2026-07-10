# Feeder installation

Tested against the [adsb.im](https://adsb.im) feeder image (ultrafeeder / readsb).
The SBS stream is available on `localhost:30003` by default.

## 1. Copy files to the feeder

```bash
scp feeder.py requirements.txt root@<feeder-ip>:/opt/flighttracker/
```

## 2. Install system dependencies

```bash
ssh root@<feeder-ip>
apt install -y espeak-ng alsa-utils
```

## 3. Create a Python virtual environment on the feeder

```bash
ssh root@<feeder-ip>

python3 -m venv /opt/flighttracker/venv
/opt/flighttracker/venv/bin/pip install -r /opt/flighttracker/requirements.txt
```

## 4. Test sound output

Verify that `espeak-ng` is working and the speaker volume is acceptable:

```bash
/opt/flighttracker/venv/bin/python /opt/flighttracker/feeder.py --test-sound
```

You should hear two spoken phrases: a generic sound test and a sample takeoff announcement. Adjust system volume if needed before continuing.

## 5. Test manually

```bash
/opt/flighttracker/venv/bin/python /opt/flighttracker/feeder.py
```

You should see log lines like:

```
2024-01-01 12:00:00 INFO ADS-B feeder starting
2024-01-01 12:00:00 INFO SBS source : localhost:30003
2024-01-01 12:00:00 INFO Server     : http://100.70.200.82:5000/sbs
2024-01-01 12:00:05 INFO Sent 42 messages -> HTTP 200
```

If the SBS port is not 30003, check your feeder config and update `SBS_PORT` in `feeder.py`.

## 6. Install as a systemd service

```bash
scp flighttracker-feeder.service root@<feeder-ip>:/etc/systemd/system/

ssh root@<feeder-ip>
systemctl daemon-reload
systemctl enable --now flighttracker-feeder
systemctl status flighttracker-feeder
```

## Verify the SBS stream is available

```bash
# Should print SBS messages to your terminal
nc localhost 30003
```

## Local altitude API

The feeder also serves a local `/api/current` endpoint (same shape as the website's),
sourced from the live SBS stream instead of the database — for a display device on
the same LAN that wants current altitude without going through the website:

```bash
curl "http://<feeder-ip>:9878/api/current?ac=PH-TGC&simple"
```

Supports the same `ac=` (registration or hex), `simple=` (plain-text altitude) and
`fake=` (synthetic test ramp) query params as the website's `/api/current`. It is
unauthenticated — only expose it on a trusted LAN, never over the public internet.

## Local PH-TGC message log

Every raw SBS message seen for PH-TGC (hex `484763`) — not just altitude readings —
is archived locally in a SQLite database (`tgc_log.db`, next to `feeder.py` by
default), for later checks or replay. This file grows without pruning and is
local-only (never synced to the website or anywhere else). Query it directly with
the `sqlite3` CLI, e.g.:

```bash
sqlite3 /opt/flighttracker/tgc_log.db "SELECT count(*), msg_type FROM messages GROUP BY msg_type"
```

## Configuration

Edit `feeder.py` to change:

| Variable | Default | Description |
|---|---|---|
| `SBS_HOST` | `localhost` | Host running dump1090/readsb |
| `SBS_PORT` | `30003` | SBS TCP port |
| `SERVER_URL` | `http://100.70.200.82:5000/sbs` | Tracking server endpoint |
| `SEND_INTERVAL` | `5` | Seconds between batches |
| `ALTITUDE_API_PORT` | `9878` | LAN altitude query endpoint for a local display device |
| `TGC_LOG_DB` (env var) | `tgc_log.db` next to `feeder.py` | Full raw-message archive for PH-TGC (checks/replay) |
