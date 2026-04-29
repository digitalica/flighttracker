# FlightTracker

Personal project to track a small set of aircraft using an ADS-B feeder.

## Architecture

```
ADS-B feeder image (adsb.im / ultrafeeder / readsb)
  └─ SBS TCP stream on :30003
       └─ adsbfeeder/feeder.py   — reads stream, POSTs batches to server every 5s
            └─ POST /sbs  ──►  website/server.py :5000
                                  stores readings in SQLite (flighttracker.db)
                                  GET /api/aircraft  — list of tracked aircraft
                                  GET /api/altitude/<hex>?minutes=N  — time-series data
                                  GET /api/status    — last-seen per aircraft
                                  GET /             — altitude graph frontend
```

The feeder filters by ICAO hex before sending; only target aircraft messages reach the server.

## Tracked aircraft

| ICAO hex | Registration | Type                        | Notes                        |
|----------|--------------|-----------------------------|------------------------------|
| 484763   | PH-TGC       | Cessna 182R Skylane         |                              |
| 48484C   | PH-GYS       | Reims-Cessna F172N Skyhawk  |                              |
| 4849B9   | PH-GOZ       | Aviat A-1 Husky             |                              |
| 4848F9   | PH-RYF       | Hughes 269C                 | helicopter, Heli Holland     |
| 484583   | PH-RIS       | Airbus H130                 | helicopter, KNSF Flight Svcs |
| 48462C   | PH-SKC       | Reims-Cessna F172N Skyhawk  |                              |
| 48459C   | PH-VHA       | Tecnam P2002JF Sierra       | Vliegschool Hilversum        |
| 484655   | PH-CBN       | Reims-Cessna F172N Skyhawk  |                              |
| 48481F   | PH-WMA       | Reims-Cessna F172P Skyhawk  |                              |
| 486237   | PH-VHY       | Cessna 172P Skyhawk         | Vliegschool Hilversum        |
| 485FD8   | PH-VHP       | Piper PA-28-161 Warrior     | Vliegschool Hilversum        |
| 4863FF   | PH-VHK       | Piper PA-28-161 Warrior     | Vliegschool Hilversum        |
| 484406   | PH-CJC       | Piper PA-28-181 Archer III  |                              |
| 4869BC   | PH-VHM       | Tecnam P2002JF Sierra       | Vliegschool Hilversum        |
| 4845BB   | PH-4B7       | Aerospool WT-9 Dynamic      | ultralight                   |
| 3E5E11   | DK-AUZ       | Scheibe SF-25C Falke        | motorglider, German (D-KAUZ) |
| 4847D7   | PH-TGA       | Reims-Cessna F150M          |                              |
| 4849B7   | PH1372       | glider                      |                              |
| 484F66   | PH1489       | glider                      |                              |
| 484B68   | PH1432       | glider                      |                              |
| 4845AE   | PH-DON       | Cessna 172P Skyhawk         |                              |

## Infrastructure

| Role    | Address       |
|---------|---------------|
| Server  | 100.70.200.82 |
| Network | Tailscale     |

## Repo layout

```
adsbfeeder/
  feeder.py                     # runs on the ADS-B feeder image
  requirements.txt              # requests
  flighttracker-feeder.service  # systemd unit
  INSTALL.md                    # feeder install steps

testserver/                     # superseded by website/; kept for reference
  server.py
  requirements.txt
  INSTALL.md

website/
  server.py                     # Flask server: ingest + SQLite + API + frontend
  templates/index.html          # Chart.js altitude graph, dark theme
  requirements.txt              # flask
  INSTALL.md                    # server install steps
  flighttracker.db              # SQLite database (created on first run, not in git)
```

## Status

- [x] Feeder client (SBS stream → HTTP batches)
- [x] Website server (ingest, SQLite storage, altitude API, Chart.js frontend)
- [x] Systemd service units for both sides
- [ ] Event detection (takeoff / landing from altitude data)

## Running locally (quick test)

```bash
# Website server
cd website && pip install -r requirements.txt && python server.py

# Feeder (needs a reachable dump1090/readsb on :30003)
cd adsbfeeder && pip install -r requirements.txt && python feeder.py
```

## Website UI

The frontend has no buttons. Interactions are:

| Click target     | Action                                      |
|------------------|---------------------------------------------|
| Chart title      | Open aircraft picker (registration list)    |
| Y-axis label     | Toggle barometric ↔ AGL altitude            |
| X-axis label     | Cycle time range: 30 min → 2 h → 4 h → 8 h → 16 h |

The subtitle below the title shows last-seen status: `now`, `X min ago`, `X h ago`, or `not today`.

Aircraft picker symbols: `●` green = active (seen < 1 min), `●` amber = sleeping (seen today), `○` grey = inactive (not seen today).

## AGL altitude

Barometric altitude from ADS-B is offset from true AGL by the local QNH error. When an aircraft
first appears (new session after >120 s silence), if its first altitude reading is in the range
−500 to +500 ft it is stored as the ground offset for that session. AGL = baro − offset.
Offsets are stored in the `agl_offsets` table and applied at query time.

## Notes

- SBS port is 30003 on most feeder images; update `SBS_PORT` in `feeder.py` if different
- A new session starts after 120 s of silence (`STALE_SECONDS` in `website/server.py`)
- Feeder buffers messages if the server is temporarily unreachable and retransmits
- The graph downsamples to max 3000 points server-side for long time ranges (`MAX_POINTS`)
- No data pruning — the SQLite database grows indefinitely (modest volume for this use case)
