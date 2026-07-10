# FlightTracker

Personal project that tracks a set of EHHV-based aircraft using an ADS-B feeder. Live at **[phtgc.nl](https://phtgc.nl)**.

## How it works

```
ADS-B feeder (adsb.im / ultrafeeder / readsb)
  └─ SBS TCP stream on :30003
       └─ feeder/feeder.py   — filters by ICAO hex, POSTs batches to server every 5 s
            └─ POST /sbs  ──►  website/server.py :5000
                                  stores readings in SQLite
                                  serves altitude graph + events frontend
```

## Features

- **Altitude graph** — barometric or AGL altitude with smoothed rate of climb, updates every 5 s
- **Events page** — takeoff, landing, altitude milestones; announced via browser speech synthesis
- **AGL correction** — ground offset computed automatically from low-altitude readings
- **Outlier filter** — transponder spikes removed before display
- **Realtime replay** — replay SBS log files at real-time pace for testing

## Repo layout

```
feeder/          # feeder client (runs on the ADS-B feeder image)
website/             # Flask server + Chart.js frontend
  server.py          # ingest, SQLite, API, event detection
  templates/         # index.html — single-page app
  Dockerfile
tools/
  replay.py          # replay an SBS log file to the server
  export.py          # CSV export
  copydata.py        # copy one day's data to today (testing)
  cleartoday.py      # wipe today's readings (testing)
tests/               # pytest unit tests
testdata/            # sample SBS log files
docker-compose.yml
```

## Running locally

```bash
# Server
cd website && pip install -r requirements.txt && python server.py

# Feeder (needs a reachable dump1090/readsb on :30003)
cd feeder && pip install -r requirements.txt && python feeder.py

# Replay a log file at real-time pace
python tools/replay.py --realtime

# Run tests
pytest tests/
```

## UI interactions

| Click target         | Action                                              |
|----------------------|-----------------------------------------------------|
| Registration (title) | Open aircraft picker                                |
| Right Y-axis label   | Toggle barometric ↔ AGL altitude                   |
| X-axis label         | Cycle time range: 30 min → 1 h → 2 h → 4 h → 16 h |

URL state (`?ac=PH-TGC&mins=30&agl=0&view=events`) is bookmarkable.

## Stack

Python · Flask · SQLite · Chart.js · Docker · Tailscale · Traefik
