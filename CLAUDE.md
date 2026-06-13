# FlightTracker

Personal project to track a small set of aircraft using an ADS-B feeder.

## Architecture

```
ADS-B feeder image (adsb.im / ultrafeeder / readsb)
  └─ SBS TCP stream on :30003
       └─ adsbfeeder/feeder.py   — reads stream, POSTs batches to server every 5s
            └─ POST /sbs  ──►  website/server.py :5000
                                  stores readings in SQLite (flighttracker.db)
                                  GET /api/aircraft           — list of tracked aircraft
                                  GET /api/altitude/<hex>?minutes=N  — {session_start, points}
                                  GET /api/status             — last-seen per aircraft
                                  GET /                       — altitude graph frontend
```

The feeder filters by ICAO hex before sending; only target aircraft messages reach the server.

## Tracked aircraft

| ICAO hex | Registration | Type                        | Notes                        |
|----------|--------------|-----------------------------|------------------------------|
| 484763   | PH-TGC       | Cessna 182R Skylane         |                              |
| 48484C   | PH-GYS       | Reims-Cessna F172N Skyhawk  |                              |
| 4849B9   | PH-GOZ       | Aviat A-1 Husky             |                              |
| 4849A0   | PH-ACX       |                             |                              |
| 484AE6   | PH-GBA       | Piper PA-18-150 Super Cub   |                              |
| 4848F9   | PH-RYF       | Hughes 269C                 | helicopter, Heli Holland     |
| 484583   | PH-RIS       | Airbus H130                 | helicopter, KNSF Flight Svcs |
| 48462C   | PH-SKC       | Reims-Cessna F172N Skyhawk  |                              |
| 48459C   | PH-VHA       | Tecnam P2002JF Sierra       | Vliegschool Hilversum        |
| 4845F0   | PH-VHD       | Tecnam P-2002 Sierra        | Vliegschool Hilversum        |
| 484608   | PH-JBC       | Cessna 172 Skyhawk          |                              |
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
| 484737   | PH-LEN       | Reims-Cessna F172N Skyhawk  |                              |
| 484846   | PH1133       | Diamond HK36TC Super Dimona | motorglider                  |
| 485E08   | PH-4T7       | TL Ultralight TL-3000 Sirius| ultralight                   |
| 484BF9   | PH-GIN       | Fuji FA-200-180 Aero Subaru |                              |
| 48462E   | PH-MFT       | Diamond DV20 Katana         |                              |
| 48487E   | PH-2X3       | TL TL-232 Condor            | ultralight                   |
| 484D14   | PH1466       | Diamond HK-36 TTC           | motorglider                  |
| 484FF2   | PH-PLP       | Van's RV-7                  |                              |
| A8B0A3   | N65909       | Cessna 172 Skyhawk (1983)   | US registration              |
| 3ECADC   | D-KRUA       | Schleicher ASG-29           | glider, German               |
| 484C49   | PH1311       | Schleicher ASK-21           | glider                       |
| A0796C   | N13FY        | North American T-6 Texan    | US registration, 1942        |

## Infrastructure

| Role      | Address           |
|-----------|-------------------|
| Server IP | 100.70.200.82     |
| Public URL| https://phtgc.nl  |
| Network   | Tailscale + Traefik|

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
  server.py                     # Flask/gunicorn server: ingest + SQLite + API + frontend
  templates/index.html          # Chart.js altitude graph, dark theme
  static/favicon.png            # browser tab icon
  requirements.txt              # flask, gunicorn
  Dockerfile                    # single-container deployment
  .dockerignore
  INSTALL.md                    # server install steps

tools/
  export.py                     # CLI CSV export tool (DB_PATH env var or default)
  copydata.py                   # Copy one day's readings to today (for testing)
  cleartoday.py                 # Delete all readings and AGL offsets for today
  replay.py                     # Replay an SBS log file to the /sbs endpoint

docker-compose.yml              # brings up the website container
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

| Click target  | Action                                                      |
|---------------|-------------------------------------------------------------|
| Chart title   | Open aircraft picker (sorted by registration)               |
| Y-axis label  | Toggle barometric ↔ AGL altitude                            |
| X-axis label  | Cycle time range: 30 min → 1 h → 2 h → 4 h → 8 h → 16 h  |

State (aircraft, time range, altitude mode) is encoded in the URL as query parameters
(`?ac=PH-TGC&mins=30&agl=0`) so pages can be bookmarked and shared.

**Subtitle** — shows last-seen status for the displayed aircraft:
- `now, 1h 3m flight time` — active (seen < 1 min), with time since session start
- `14 min ago` / `2 h ago` — sleeping (seen today, not recently)
- `not today` — inactive (no readings since midnight)

**Aircraft picker** symbols: `●` green = active, `●` amber = sleeping, `○` grey = inactive.

**Graph** — two lines share the time axis:
- Blue (left axis): altitude in ft, barometric or AGL depending on toggle
- Orange (right axis): smoothed rate of climb in ft/min, always centered on zero
- Lines are broken at gaps > 30 s in the data (no interpolation across silences)
- Graph always spans the full selected time range even with sparse data

## AGL altitude

Barometric altitude from ADS-B is offset from true AGL by the local QNH error. When an aircraft
first appears (new session after >120 s silence), if its first altitude reading is in the range
−500 to +500 ft it is stored as the ground offset for that session. AGL = baro − offset.
Offsets are stored in the `agl_offsets` table and applied at query time.

## Rate of climb

Computed server-side from the stored altitude readings using a 60-second sliding time window:
for each point, the rate is `(alt_at_+30s − alt_at_−30s) / dt × 60`. This smooths out
transponder noise while staying responsive to real climbs and descents.

## Data quality

- **Outlier filter** — readings implying a climb/descent rate > 1000 ft/s are silently dropped
  during ingest (`MAX_CLIMB_RATE` in `website/server.py`)
- **Session detection** — a new session starts after 120 s of silence; first altitude in
  [−500, +500] ft is stored as the AGL ground offset for that session

## Notes

- SBS port is 30003 on most feeder images; update `SBS_PORT` in `feeder.py` if different
- Feeder buffers messages if the server is temporarily unreachable and retransmits
- The graph downsamples to max 3000 points server-side for long time ranges (`MAX_POINTS`)
- No data pruning — the SQLite database grows indefinitely (modest volume for this use case)
