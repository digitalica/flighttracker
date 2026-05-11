#!/usr/bin/env python3
"""
Replay SBS messages from a log file to the /sbs endpoint.

By default the date in each SBS message (field 6) is replaced with today's date
so replayed data shows up as current. Use --no-redate to keep original dates.
Use --align to shift all timestamps so the first message starts at 00:00 today.

Usage
-----
python replay.py
python replay.py --file data/tgc20260509.log
python replay.py --file data/tgc20260509.log --url http://localhost:5000/sbs
python replay.py --file data/tgc20260509.log --batch 50
python replay.py --no-redate
python replay.py --align
python replay.py --realtime
python replay.py --align --realtime
"""

import argparse
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

DEFAULT_FILE         = Path(__file__).parent.parent / "testdata" / "tgc20260509.log"
DEFAULT_URL          = "http://localhost:5000/sbs"
BATCH_SIZE           = 50
REALTIME_WINDOW_SECS = 1  # group messages into 1-second buckets to mimic the feeder
HEARTBEAT_INTERVAL   = 2  # send empty POST at most every N seconds when idle


def is_valid(line: str) -> bool:
    if not line.startswith("MSG,"):
        return False
    return len(line.split(",")) >= 11


def redate(line: str, today: str) -> str:
    """Replace the date field (index 6) in an SBS message with today's date."""
    parts = line.split(",")
    parts[6] = today
    return ",".join(parts)


def _parse_line_ts(line: str) -> datetime | None:
    parts = line.split(",")
    try:
        date_str, time_str = parts[6].strip(), parts[7].strip()
    except IndexError:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            pass
    return None


def apply_delta(line: str, delta: timedelta) -> str:
    """Shift the timestamp (fields 6+7) in an SBS message by delta."""
    ts = _parse_line_ts(line)
    if ts is None:
        return line
    new_ts = ts + delta
    parts = line.split(",")
    parts[6] = new_ts.strftime("%Y/%m/%d")
    parts[7] = new_ts.strftime("%H:%M:%S.") + f"{new_ts.microsecond // 1000:03d}"
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
                        help="Keep original dates instead of replacing with today")
    parser.add_argument("--align", action="store_true",
                        help="Shift all timestamps so the first message starts at 00:00 today")
    parser.add_argument("--realtime", action="store_true",
                        help="Pace batches according to message timestamps (real-time replay)")
    args = parser.parse_args()

    log_path = Path(args.file)
    if not log_path.exists():
        print(f"File not found: {log_path}")
        return 1

    today = date.today().strftime("%Y/%m/%d")

    lines = log_path.read_text(encoding="ascii", errors="replace").splitlines()
    valid = [l.strip() for l in lines if is_valid(l.strip())]
    skipped = len(lines) - len(valid)

    if args.align:
        timestamps = [_parse_line_ts(l) for l in valid]
        first_ts = min((t for t in timestamps if t is not None), default=None)
        if first_ts is not None:
            midnight = datetime.strptime(today, "%Y/%m/%d")
            delta = midnight - first_ts
            valid = [apply_delta(l, delta) for l in valid]
        time_label = f"aligned → first event at 00:00 today"
    elif not args.no_redate:
        valid = [redate(l, today) for l in valid]
        time_label = f"redate → {today}"
    else:
        time_label = "no (keeping original)"

    if args.realtime:
        # Group messages into REALTIME_WINDOW_SECS buckets, keyed by window start time
        timestamps = [_parse_line_ts(l) for l in valid]
        ts_valid   = [t for t in timestamps if t is not None]
        first_ts   = min(ts_valid) if ts_valid else None
        if first_ts:
            batches = []
            bucket_msgs, bucket_ts = [], None
            for line, ts in zip(valid, timestamps):
                if ts is None:
                    if bucket_msgs: bucket_msgs.append(line)
                    continue
                window = int((ts - first_ts).total_seconds() // REALTIME_WINDOW_SECS)
                if bucket_ts is None:
                    bucket_ts = window
                if window != bucket_ts:
                    batches.append((first_ts + timedelta(seconds=bucket_ts * REALTIME_WINDOW_SECS), bucket_msgs))
                    bucket_msgs, bucket_ts = [], window
                bucket_msgs.append(line)
            if bucket_msgs:
                batches.append((first_ts + timedelta(seconds=bucket_ts * REALTIME_WINDOW_SECS), bucket_msgs))
        else:
            batches = [(None, valid)]
        mode_label = f"real-time ({REALTIME_WINDOW_SECS}s windows, {len(batches)} batches)"
    else:
        batches    = [(None, valid[i:i + args.batch]) for i in range(0, len(valid), args.batch)]
        first_ts   = None
        mode_label = "as fast as possible"

    print(f"File   : {log_path}")
    print(f"Lines  : {len(lines)} total, {len(valid)} valid, {skipped} skipped")
    print(f"Time   : {time_label}")
    print(f"Mode   : {mode_label}")
    print(f"Target : {args.url}")
    print(f"Batches: {len(batches)}")

    answer = input("Send? [y/N] ")
    if answer.strip().lower() != "y":
        print("Aborted.")
        return 1

    sent = 0
    errors = 0
    start_wall = _time.monotonic()
    last_post_wall = -HEARTBEAT_INTERVAL  # allow immediate first heartbeat if needed

    for batch_idx, (batch_ts, batch) in enumerate(batches):
        if args.realtime and first_ts and batch_ts:
            target = (batch_ts - first_ts).total_seconds()
            while True:
                remaining = target - (_time.monotonic() - start_wall)
                if remaining <= 0:
                    break
                since_last = _time.monotonic() - last_post_wall
                if since_last >= HEARTBEAT_INTERVAL:
                    try:
                        requests.post(args.url, json={"messages": []}, timeout=10)
                        last_post_wall = _time.monotonic()
                    except requests.exceptions.RequestException:
                        pass
                    remaining = target - (_time.monotonic() - start_wall)
                    print(f"\r  heartbeat  (next batch in {remaining:.0f}s, {sent}/{len(valid)} msgs sent)   ",
                          end="", flush=True)
                _time.sleep(min(HEARTBEAT_INTERVAL, max(0, remaining)))

        try:
            resp = requests.post(args.url, json={"messages": batch}, timeout=10)
            resp.raise_for_status()
            sent += len(batch)
            last_post_wall = _time.monotonic()
            print(f"\r  batch {batch_idx + 1}/{len(batches)}  {sent}/{len(valid)} msgs sent   ",
                  end="", flush=True)
        except requests.exceptions.RequestException as exc:
            errors += 1
            print(f"\nBatch failed: {exc}", file=sys.stderr)
            if errors >= 3:
                print("Too many errors, aborting.", file=sys.stderr)
                return 1

    print(f"\nDone. {sent} messages sent, {errors} batch errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
