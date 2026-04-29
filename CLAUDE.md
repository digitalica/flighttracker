# FlightTracker

Personal project to track a small set of aircraft using an ADS-B feeder.

## Architecture

```
ADS-B feeder image (adsb.im / ultrafeeder / readsb)
  └─ SBS TCP stream on :30003
       └─ adsbfeeder/feeder.py   — reads stream, POSTs batches to server every 5s
            └─ POST /sbs  ──►  testserver/server.py :5000
                                  GET /tracked  — target aircraft only
                                  GET /all      — all aircraft in state
```

The feeder filters by ICAO hex before sending; only target aircraft messages reach the server.

## Tracked aircraft

| ICAO hex | Registration | Notes  |
|----------|-------------|--------|
| 484763   | PH-TGC      |        |
| 48484C   | PH-GYS      |        |
| 4849B9   | PH-GOZ      |        |
| 4848F9   | PH-RYF      |        |
| 484583   | PH-RIS      |        |
| 48462C   | PH-SKC      |        |
| 48459C   | PH-VHA      |        |
| 484655   | PH-CBN      |        |
| 48481F   | PH-WMA      |        |
| 486237   | PH-VHY      |        |
| 485FD8   | PH-VHP      |        |
| 4863FF   | PH-VHK      |        |
| 484406   | PH-CJC      |        |
| 4869BC   | PH-VHM      |        |
| 4845BB   | PH-4B7      |        |
| 3E5E11   | DK-AUZ      |        |
| 4847D7   | PH-TGA      |        |

## Infrastructure

| Role        | Address        |
|-------------|----------------|
| Test server | 100.70.200.82  |
| Network     | Tailscale      |

## Repo layout

```
adsbfeeder/
  feeder.py                     # runs on the ADS-B feeder image
  requirements.txt              # requests
  flighttracker-feeder.service  # systemd unit
  INSTALL.md                    # feeder install steps

testserver/
  server.py                     # Flask test server
  requirements.txt              # flask
  INSTALL.md                    # server install steps

website/                        # not started yet
```

## Status

- [x] Feeder client (SBS stream → HTTP batches)
- [x] Test server (ingest, parse, /tracked, /all endpoints)
- [x] Systemd service units for both sides
- [ ] Website / frontend (planned, directory reserved)

## Running locally (quick test)

```bash
# Server
cd testserver && pip install -r requirements.txt && python server.py

# Feeder (needs a reachable dump1090/readsb on :30003)
cd adsbfeeder && pip install -r requirements.txt && python feeder.py
```

## Notes

- SBS port is 30003 on most feeder images; update `SBS_PORT` in `feeder.py` if different
- Aircraft state expires after 120s without a message (configurable: `STALE_SECONDS` in server.py)
- Feeder buffers messages if the server is temporarily unreachable and retransmits
