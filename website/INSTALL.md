# Website server — installation & operations

Runs on the tracking server (`100.70.200.82`). Receives SBS batches from the feeder,
stores altitude readings in SQLite, and serves the altitude graph at `https://phtgc.nl`.

The server runs as a single Docker container, managed by Traefik (reverse proxy + TLS).

---

## Initial setup (first time only)

### Prerequisites

- Docker + Docker Compose installed
- Traefik running with an external network named `proxy` and a `letsencrypt` cert resolver
- Port 443 open; DNS for `phtgc.nl` and `www.phtgc.nl` pointing to the server

### Deploy

```bash
# On the server
mkdir -p /opt/flighttracker
cd /opt/flighttracker
# Copy docker-compose.yml from the repo (or pull via git)
docker compose up -d
```

Traefik picks up the labels automatically and requests a Let's Encrypt certificate.
The SQLite database is stored in a named Docker volume (`db_data`) and persists across restarts.

---

## Updating the application (code changes)

Pushing to `main` triggers a GitHub Actions workflow that builds and pushes a new image to
`ghcr.io/digitalica/flighttracker:latest`. To deploy it on the server:

```bash
cd /opt/flighttracker
docker compose pull
docker compose up -d
```

No downtime beyond the container restart (~1 second).

---

## Updating the configuration (docker-compose.yml changes)

When `docker-compose.yml` changes (Traefik labels, environment variables, volumes, etc.),
copy the updated file to the server and re-apply:

```bash
# From your local machine
scp docker-compose.yml root@100.70.200.82:/opt/flighttracker/

# Then on the server
cd /opt/flighttracker
docker compose up -d
```

Docker Compose only recreates the container if its configuration actually changed.

---

## Useful commands

```bash
# Check container status
docker compose ps

# Tail live logs
docker compose logs -f

# Restart container
docker compose restart

# Open a shell inside the container
docker compose exec website bash
```

---

## Database backup

The SQLite database lives in the `db_data` Docker volume. To download a copy:

```bash
# Via the built-in endpoint (Tailscale required)
curl -o flighttracker.db http://100.70.200.82:5000/db
# or
curl -o flighttracker.db https://phtgc.nl/db
```

Or copy directly from the volume:

```bash
# On the server
docker run --rm -v flighttracker_db_data:/data -v $(pwd):/out alpine \
  cp /data/flighttracker.db /out/
```

---

## Exporting data to CSV

Use `tools/export.py` — it reads the database file directly, no server restart needed.

```bash
# Download the database first (see above), then locally:
DB_PATH=./flighttracker.db python tools/export.py --today --ac PH-TGC --out phtgc_today.csv
```

Output columns: `timestamp, registration, icao_hex, altitude_ft, lat, lon, on_ground`.
