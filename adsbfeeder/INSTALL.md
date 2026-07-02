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
apt install -y espeak-ng
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

## Configuration

Edit `feeder.py` to change:

| Variable | Default | Description |
|---|---|---|
| `SBS_HOST` | `localhost` | Host running dump1090/readsb |
| `SBS_PORT` | `30003` | SBS TCP port |
| `SERVER_URL` | `http://100.70.200.82:5000/sbs` | Tracking server endpoint |
| `SEND_INTERVAL` | `5` | Seconds between batches |
