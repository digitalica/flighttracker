#!/usr/bin/env python3
"""
Replay SBS messages from a log file to the /sbs endpoint.

By default the date in each SBS message (field 6) is replaced with today's date
so replayed data shows up as current. Use --no-redate to keep original dates.

Usage
-----
python replay.py
python replay.py --file data/tgc20260509.log
python replay.py --file data/tgc20260509.log --url http://localhost:5000/sbs
python replay.py --file data/tgc20260509.log --batch 50
python replay.py --no-redate
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import requests

DEFAULT_FILE = Path(__file__).parent.parent / "testdata" / "tgc20260509.log"
DEFAULT_URL  = "http://localhost:5000/sbs"
BATCH_SIZE   = 50


def is_valid(line: str) -> bool:
    if not line.startswith("MSG,"):
        return False
    return len(line.split(",")) >= 11


def redate(line: str, today: str) -> str:
    """Replace the date field (index 6) in an SBS message with today's date."""
    parts = line.split(",")
    parts[6] = today
    return ",".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Replay SBS log file to /sbs endpoint.")
    parser.add_argument("--file", default=str(DEFAULT_FILE), metavar="PATH",
                        help=f"Log file to replay (default: {DEFAULT_FILE.name})")
    parser.add_argument("--url", default=DEFAULT_URL, metavar="URL",
                        help=f"Target endpoint (default: {DEFAULT_URL})")
    parser.add_argument("--batch", default=BATCH_SIZE, type=int, metavar="N",
                        help=f"Messages per POST (default: {BATCH_SIZE})")
    parser.add_argument("--no-redate", action="store_true",
                        help="Keep original dates from the log file instead of replacing with today")
    args = parser.parse_args()

    log_path = Path(args.file)
    if not log_path.exists():
        print(f"File not found: {log_path}")
        return 1

    today = date.today().strftime("%Y/%m/%d")

    lines = log_path.read_text(encoding="ascii", errors="replace").splitlines()
    valid = [l.strip() for l in lines if is_valid(l.strip())]
    if not args.no_redate:
        valid = [redate(l, today) for l in valid]
    skipped = len(lines) - len(valid)

    print(f"File   : {log_path}")
    print(f"Lines  : {len(lines)} total, {len(valid)} valid, {skipped} skipped")
    print(f"Redate : {'no (keeping original)' if args.no_redate else f'yes → {today}'}")
    print(f"Target : {args.url}")
    print(f"Batches: {-(-len(valid) // args.batch)} × {args.batch}")

    answer = input("Send? [y/N] ")
    if answer.strip().lower() != "y":
        print("Aborted.")
        return 1

    sent = 0
    errors = 0
    for i in range(0, len(valid), args.batch):
        batch = valid[i:i + args.batch]
        try:
            resp = requests.post(args.url, json={"messages": batch}, timeout=10)
            resp.raise_for_status()
            sent += len(batch)
            print(f"\r  {sent}/{len(valid)} messages sent", end="", flush=True)
        except requests.exceptions.RequestException as exc:
            errors += 1
            print(f"\nBatch {i // args.batch + 1} failed: {exc}", file=sys.stderr)
            if errors >= 3:
                print("Too many errors, aborting.", file=sys.stderr)
                return 1

    print(f"\nDone. {sent} messages sent, {errors} batch errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
