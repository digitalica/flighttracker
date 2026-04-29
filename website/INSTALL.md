# Website server installation

Runs on the tracking server (100.70.200.82). Receives SBS batches from the feeder,
stores altitude readings in SQLite, and serves the altitude graph at port 5000.

## 1. Install dependencies

```bash
python3 -m venv /opt/flighttracker-website/venv
/opt/flighttracker-website/venv/bin/pip install -r requirements.txt
cp -r server.py templates/ /opt/flighttracker-website/
```

## 2. Test manually

```bash
/opt/flighttracker-website/venv/bin/python /opt/flighttracker-website/server.py
```

Then open `http://100.70.200.82:5000` in a browser (Tailscale required).

## 3. Install as a systemd service

```bash
cat > /etc/systemd/system/flighttracker-website.service << 'EOF'
[Unit]
Description=FlightTracker website
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/flighttracker-website
ExecStart=/opt/flighttracker-website/venv/bin/python server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now flighttracker-website
systemctl status flighttracker-website
```

## 4. Check it works

```bash
# Aircraft list
curl http://localhost:5000/api/aircraft | python3 -m json.tool

# Altitude data for PH-TGC (last 30 min)
curl 'http://localhost:5000/api/altitude/484763?minutes=30' | python3 -m json.tool
```

## Running locally (quick test)

```bash
cd website && pip install -r requirements.txt && python server.py
```

The database (`flighttracker.db`) is created automatically on first run in the same directory as `server.py`.

## Replacing the test server

This server uses the same `POST /sbs` contract as the test server, so the feeder needs no changes — just point `SERVER_URL` in `feeder.py` at this server's address if it runs on a different host.
