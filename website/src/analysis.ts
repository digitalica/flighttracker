/**
 * Pure analysis functions for FlightTracker.
 * No Express/DB imports — fully testable with Vitest.
 */

export interface ParsedMessage {
  hex: string;
  msg_type: string;
  ts?: string;
  altitude?: number;
  lat?: number;
  lon?: number;
  on_ground?: number;
}

const SBS_IDX = {
  msg_type: 1,
  hex: 4,
  date_gen: 6,
  time_gen: 7,
  altitude: 11,
  lat: 14,
  lon: 15,
  on_ground: 21,
} as const;

export function parseSbsLine(line: string): ParsedMessage | null {
  if (!line.startsWith("MSG,")) return null;
  const parts = line.split(",");
  if (parts.length < 11) return null;

  const get = (idx: number): string | null => {
    if (idx >= parts.length) return null;
    const v = parts[idx].trim();
    return v === "" ? null : v;
  };

  const msg_type = get(SBS_IDX.msg_type);
  const hex_raw = get(SBS_IDX.hex);
  if (!hex_raw) return null;

  const result: ParsedMessage = {
    hex: hex_raw.toLowerCase(),
    msg_type: msg_type ?? "",
  };

  const date_str = get(SBS_IDX.date_gen);
  const time_str = get(SBS_IDX.time_gen);
  if (date_str && time_str) {
    // Parse "YYYY/MM/DD HH:MM:SS[.mmm]" as UTC
    const combined = `${date_str} ${time_str}`;
    // Convert YYYY/MM/DD to YYYY-MM-DD for Date parsing
    const iso = combined.replace(/\//g, "-").replace(" ", "T") + "Z";
    const d = new Date(iso);
    if (!isNaN(d.getTime())) {
      result.ts = d.toISOString();
    }
  }

  if (msg_type === "2" || msg_type === "3" || msg_type === "5" || msg_type === "7") {
    const altStr = get(SBS_IDX.altitude);
    if (altStr !== null) {
      const alt = parseInt(altStr, 10);
      if (!isNaN(alt)) result.altitude = alt;
    }
  }
  if (msg_type === "2" || msg_type === "3") {
    const latStr = get(SBS_IDX.lat);
    const lonStr = get(SBS_IDX.lon);
    if (latStr !== null) {
      const lat = parseFloat(latStr);
      if (!isNaN(lat)) result.lat = lat;
    }
    if (lonStr !== null) {
      const lon = parseFloat(lonStr);
      if (!isNaN(lon)) result.lon = lon;
    }
  }

  if (parts.length > SBS_IDX.on_ground) {
    const gnd = get(SBS_IDX.on_ground);
    if (gnd !== null) {
      result.on_ground = gnd === "1" || gnd === "-1" ? 1 : 0;
    }
  }

  if (result.altitude === undefined && (result.lat === undefined || result.lon === undefined)) {
    return null;
  }

  return result;
}

const STALE_SECONDS = 120;

export interface AltRow {
  ts: string;
  alt_baro: number;
}

export function filterAltitudeOutliers(
  rows: AltRow[],
  maxRateFtPerMin = 5000,
): AltRow[] {
  if (rows.length < 2) return [...rows];

  const times = rows.map((r) => new Date(r.ts).getTime() / 1000);
  const alts = rows.map((r) => r.alt_baro);
  const n = rows.length;
  const keep = new Array<boolean>(n).fill(true);

  for (let i = 0; i < n; i++) {
    const rates: number[] = [];
    for (const j of [i - 1, i + 1]) {
      if (j < 0 || j >= n) continue;
      const dt = Math.abs(times[i] - times[j]);
      if (dt > 0 && dt <= STALE_SECONDS) {
        rates.push((Math.abs(alts[i] - alts[j]) / dt) * 60);
      }
    }
    if (rates.length > 0 && rates.every((r) => r > maxRateFtPerMin)) {
      keep[i] = false;
    }
  }

  return rows.filter((_, i) => keep[i]);
}

export function computeRoc(rows: AltRow[], windowSecs = 15): number[] {
  const n = rows.length;
  if (n < 2) return new Array<number>(n).fill(0);

  const times = rows.map((r) => new Date(r.ts).getTime() / 1000);
  const alts = rows.map((r) => r.alt_baro);
  const result: number[] = [];

  for (let i = 0; i < n; i++) {
    const t = times[i];
    let lo = i;
    let hi = i;
    while (lo > 0 && times[lo - 1] >= t - windowSecs) lo--;
    while (hi < n - 1 && times[hi + 1] <= t + windowSecs) hi++;
    const dt = times[hi] - times[lo];
    result.push(dt > 0 ? Math.round(((alts[hi] - alts[lo]) / dt) * 60) : 0);
  }

  return result;
}

export function computeAglOffset(rows: { alt_baro: number }[]): number {
  const candidates = rows.map((r) => r.alt_baro).filter((a) => a < 1000);
  if (candidates.length === 0) return 0;

  const sortedUnique = [...new Set(candidates)].sort((a, b) => a - b);
  const twoLowest = new Set(sortedUnique.slice(0, 2));
  const matching = candidates.filter((a) => twoLowest.has(a));
  const avg = matching.reduce((s, a) => s + a, 0) / matching.length;
  return Math.round(avg / 100) * 100;
}

export interface Event {
  type: string;
  ts: string;
}

const THRESHOLDS = [
  { trigUp: 300, trigDown: 100, up: "takeoff", down: "landing" },
  { trigUp: 3100, trigDown: 2900, up: "climbing_3000", down: "descending_3000" },
  { trigUp: 5600, trigDown: 5400, up: "climbing_5500", down: "descending_5500" },
];

const TOUCH_AND_GO_SECS = 90;
const INACTIVE_SECS_EVENTS = 30;

export function detectEvents(rows: AltRow[], aglOffset: number): Event[] {
  const events: Event[] = [];
  // null = unknown, true = above, false = below
  const states: (boolean | null)[] = new Array(THRESHOLDS.length).fill(null);
  let descentFired = false;

  if (rows.length > 0) {
    events.push({ type: "active", ts: rows[0].ts });
  }

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (i > 0) {
      const dt =
        (new Date(row.ts).getTime() - new Date(rows[i - 1].ts).getTime()) / 1000;
      if (dt > INACTIVE_SECS_EVENTS) {
        events.push({ type: "inactive", ts: rows[i - 1].ts });
        events.push({ type: "active", ts: row.ts });
      }
      if (dt > STALE_SECONDS) {
        for (let k = 0; k < states.length; k++) states[k] = null;
        descentFired = false;
      }
    }

    const agl = row.alt_baro - aglOffset;

    for (let j = 0; j < THRESHOLDS.length; j++) {
      const { trigUp, trigDown, up: upName, down: downName } = THRESHOLDS[j];
      const s = states[j];

      if (s === null) {
        if (agl > trigUp) states[j] = true;
        else if (agl < trigDown) states[j] = false;
      } else if (s === true && agl < trigDown) {
        const isAltitudeDescent = downName !== "landing";
        if (!(isAltitudeDescent && descentFired)) {
          events.push({ type: downName, ts: row.ts });
        }
        if (isAltitudeDescent) descentFired = true;
        states[j] = false;
      } else if (s === false && agl > trigUp) {
        events.push({ type: upName, ts: row.ts });
        if (upName === "takeoff") descentFired = false;
        states[j] = true;
      }
    }
  }

  if (rows.length > 0) {
    const lastDt =
      (Date.now() - new Date(rows[rows.length - 1].ts).getTime()) / 1000;
    if (lastDt > INACTIVE_SECS_EVENTS) {
      events.push({ type: "inactive", ts: rows[rows.length - 1].ts });
    }
  }

  // Merge landing + takeoff within 90s into touch_and_go
  const merged: Event[] = [];
  let i = 0;
  while (i < events.length) {
    const ev = events[i];
    if (
      ev.type === "landing" &&
      i + 1 < events.length &&
      events[i + 1].type === "takeoff"
    ) {
      const gap =
        (new Date(events[i + 1].ts).getTime() - new Date(ev.ts).getTime()) / 1000;
      if (gap <= TOUCH_AND_GO_SECS) {
        merged.push({ type: "touch_and_go", ts: events[i + 1].ts });
        i += 2;
        continue;
      }
    }
    merged.push(ev);
    i++;
  }

  return merged;
}

export function findSessionStartQuery(
  icaoHex: string,
  staleSeconds: number,
): { sql: string; params: [string, string, number] } {
  // Returns the SQL query for finding the session start — used by server.ts
  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const sql = `
    WITH ordered AS (
      SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
      FROM readings
      WHERE icao_hex = ? AND ts >= ?
    ),
    starts AS (
      SELECT ts FROM ordered
      WHERE prev_ts IS NULL
         OR (julianday(ts) - julianday(prev_ts)) * 86400 > ?
    )
    SELECT MAX(ts) AS session_start FROM starts
  `;
  return { sql, params: [icaoHex, since, staleSeconds] };
}
