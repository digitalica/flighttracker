#!/usr/bin/env python3
"""
Copy flight readings from one date to today.

Useful for testing or demo purposes when no aircraft are currently flying.
Copies readings, shifting timestamps to today.

Usage
-----
# Copy yesterday's data to today (default)
python copydata.py

# Copy data from a specific date
python copydata.py --date 2026-05-06

# Use a specific database
python copydata.py --db /data/flighttracker.db
"""

import argparse
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "website" / "flighttracker.db"))


def main():
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(description="Copy flight data from a date to today.")
    parser.add_argument("--date", default=yesterday, metavar="YYYY-MM-DD",
                        help=f"Source date to copy from (default: {yesterday})")
    parser.add_argument("--db", default=str(DB_PATH), metavar="PATH",
                        help="Path to SQLite database")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    source_date = args.date
    today = date.today().isoformat()

    if source_date == today:
        print("Source date is today — nothing to copy.")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Check if today already has data
        count_today = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE ts >= ? AND ts < ?",
            (f"{today}T00:00:00", f"{today}T23:59:59.999999"),
        ).fetchone()[0]

        if count_today > 0:
            answer = input(f"Today ({today}) already has {count_today} readings. Clear and overwrite? [y/N] ")
            if answer.strip().lower() != "y":
                print("Aborted.")
                return 1
            conn.execute("DELETE FROM readings WHERE ts >= ? AND ts < ?",
                         (f"{today}T00:00:00", f"{today}T23:59:59.999999"))
            print(f"Cleared {count_today} readings for today.")

        # Fetch source readings
        readings = conn.execute(
            "SELECT icao_hex, ts, alt_baro, lat, lon, on_ground FROM readings "
            "WHERE ts >= ? AND ts < ? ORDER BY ts",
            (f"{source_date}T00:00:00", f"{source_date}T23:59:59.999999"),
        ).fetchall()

        if not readings:
            print(f"No readings found for {source_date}.")
            return 1

        delta = date.today() - date.fromisoformat(source_date)

        def shift(ts: str) -> str:
            return (datetime.fromisoformat(ts) + delta).isoformat()

        new_readings = [
            (r["icao_hex"], shift(r["ts"]), r["alt_baro"], r["lat"], r["lon"], r["on_ground"])
            for r in readings
        ]
        conn.executemany(
            "INSERT INTO readings (icao_hex, ts, alt_baro, lat, lon, on_ground) VALUES (?,?,?,?,?,?)",
            new_readings,
        )

        conn.commit()
        print(f"Copied {len(new_readings)} readings from {source_date} → {today}.")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
