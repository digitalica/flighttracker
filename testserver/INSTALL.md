# Test server installation

Runs on the tracking server (100.70.200.82). Receives SBS batches from the feeder
and exposes simple HTTP endpoints to inspect aircraft state.

## 1. Install dependencies

```bash
python3 -m venv /opt/flighttracker-server/venv
/opt/flighttracker-server/venv/bin/pip install -r requirements.txt
cp server.py /opt/flighttracker-server/
```

## 2. Test manually

```bash
/opt/flighttracker-server/venv/bin/python /opt/flighttracker-server/server.py
```

Expected output:

```
FlightTracker test server
Tracking: PH-TGC (484763), PH-GYS (48484c), PH-GOZ (4849b9), 461FA8 (461fa8)
Endpoints:
  POST http://0.0.0.0:5000/sbs      <- feeder sends here
  GET  http://0.0.0.0:5000/tracked  <- your target aircraft
  GET  http://0.0.0.0:5000/all      <- everything
```

## 3. Install as a systemd service

```bash
cat > /etc/systemd/system/flighttracker-server.service << 'EOF'
[Unit]
Description=FlightTracker test server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/opt/flighttracker-server/venv/bin/python /opt/flighttracker-server/server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now flighttracker-server
systemctl status flighttracker-server
```

## 4. Check it works

```bash
# Should return your tracked aircraft (null if feeder not yet connected)
curl http://localhost:5000/tracked | python3 -m json.tool

# All aircraft currently in state
curl http://localhost:5000/all | python3 -m json.tool
```

## Firewall

Make sure port 5000 is reachable from the feeder (Tailscale handles this if both are on the same network):

```bash
# If using ufw:
ufw allow 5000/tcp
```
