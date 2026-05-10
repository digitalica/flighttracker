#!/usr/bin/env python3
"""
Delete all readings and AGL offsets for today from the database.

Usage
-----
python cleartoday.py
python cleartoday.py --db /data/flighttracker.db
"""

import argparse
import os
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "website" / "flighttracker.db"))


def main():
    parser = argparse.ArgumentParser(description="Clear today's flight data from the database.")
    parser.add_argument("--db", default=str(DB_PATH), metavar="PATH",
                        help="Path to SQLite database")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    today = date.today().isoformat()
    lo, hi = f"{today}T00:00:00", f"{today}T23:59:59.999999"

    conn = sqlite3.connect(db_path)
    try:
        readings = conn.execute("SELECT COUNT(*) FROM readings WHERE ts >= ? AND ts < ?", (lo, hi)).fetchone()[0]
        offsets  = conn.execute("SELECT COUNT(*) FROM agl_offsets WHERE session_start >= ? AND session_start < ?", (lo, hi)).fetchone()[0]

        if readings == 0 and offsets == 0:
            print(f"No data for today ({today}).")
            return 0

        answer = input(f"Delete {readings} readings and {offsets} AGL offsets for {today}? [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

        conn.execute("DELETE FROM readings WHERE ts >= ? AND ts < ?", (lo, hi))
        conn.execute("DELETE FROM agl_offsets WHERE session_start >= ? AND session_start < ?", (lo, hi))
        conn.commit()
        print(f"Deleted {readings} readings and {offsets} AGL offsets for {today}.")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
