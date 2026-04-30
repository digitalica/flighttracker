#!/usr/bin/env python3
"""
Export flight readings to CSV.

Usage examples
--------------
# All data
python export.py

# Specific date
python export.py --date 2026-04-30

# Today
python export.py --today

# Specific aircraft (registration or ICAO hex)
python export.py --ac PH-TGC
python export.py --ac 484763

# Combined
python export.py --date 2026-04-30 --ac PH-TGC

# Write to file instead of stdout
python export.py --date 2026-04-30 --out data.csv
"""

import argparse
import csv
import sqlite3
import sys
from datetime import date, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "flighttracker.db"

TARGET_AIRCRAFT = {
    "484763": "PH-TGC",
    "48484c": "PH-GYS",
    "4849b9": "PH-GOZ",
    "4848f9": "PH-RYF",
    "484583": "PH-RIS",
    "48462c": "PH-SKC",
    "48459c": "PH-VHA",
    "484655": "PH-CBN",
    "48481f": "PH-WMA",
    "486237": "PH-VHY",
    "485fd8": "PH-VHP",
    "4863ff": "PH-VHK",
    "484406": "PH-CJC",
    "4869bc": "PH-VHM",
    "4845bb": "PH-4B7",
    "3e5e11": "DK-AUZ",
    "4847d7": "PH-TGA",
    "4849b7": "PH1372",
    "484f66": "PH1489",
    "484b68": "PH1432",
    "4845ae": "PH-DON",
    "484737": "PH-LEN",
    "484846": "PH1133",
    "485e08": "PH-4T7",
    "484bf9": "PH-GIN",
    "48462e": "PH-MFT",
}

# Reverse map: registration (lowercase) -> hex
_REG_TO_HEX = {reg.lower(): hex_ for hex_, reg in TARGET_AIRCRAFT.items()}


def resolve_ac(ac: str) -> str:
    """Return ICAO hex for a registration or hex string, or exit with an error."""
    lower = ac.lower()
    if lower in TARGET_AIRCRAFT:
        return lower
    if lower in _REG_TO_HEX:
        return _REG_TO_HEX[lower]
    print(f"Unknown aircraft: {ac!r}. Known registrations:", file=sys.stderr)
    for h, r in sorted(TARGET_AIRCRAFT.items(), key=lambda x: x[1]):
        print(f"  {r} ({h})", file=sys.stderr)
    sys.exit(1)


def export(date_str: str | None, hex_filter: str | None, out):
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conditions = []
    params = []

    if date_str:
        conditions.append("ts >= ? AND ts < ?")
        params += [f"{date_str}T00:00:00", f"{date_str}T23:59:59.999999"]

    if hex_filter:
        conditions.append("icao_hex = ?")
        params.append(hex_filter)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"""
        SELECT ts, icao_hex, alt_baro, lat, lon, on_ground
        FROM readings
        {where}
        ORDER BY ts
    """

    rows = conn.execute(query, params).fetchall()
    conn.close()

    writer = csv.writer(out)
    writer.writerow(["timestamp", "registration", "icao_hex", "altitude_ft", "lat", "lon", "on_ground"])

    for row in rows:
        hex_ = row["icao_hex"].lower()
        reg = TARGET_AIRCRAFT.get(hex_, hex_)
        on_ground = {1: "yes", 0: "no"}.get(row["on_ground"], "")
        writer.writerow([
            row["ts"],
            reg,
            hex_,
            row["alt_baro"] if row["alt_baro"] is not None else "",
            row["lat"] if row["lat"] is not None else "",
            row["lon"] if row["lon"] is not None else "",
            on_ground,
        ])

    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Export flight readings to CSV.")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Filter by date")
    parser.add_argument("--today", action="store_true", help="Filter by today's date")
    parser.add_argument("--ac", metavar="REG_OR_HEX", help="Filter by registration or ICAO hex")
    parser.add_argument("--out", metavar="FILE", help="Output file (default: stdout)")
    args = parser.parse_args()

    if args.today and args.date:
        parser.error("Use either --date or --today, not both.")

    date_str = None
    if args.today:
        date_str = date.today().isoformat()
    elif args.date:
        date_str = args.date

    hex_filter = resolve_ac(args.ac) if args.ac else None

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            count = export(date_str, hex_filter, f)
        print(f"Wrote {count} rows to {args.out}")
    else:
        count = export(date_str, hex_filter, sys.stdout)
        print(f"# {count} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
