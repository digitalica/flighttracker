#!/usr/bin/env node
/**
 * FlightTracker website server — TypeScript/Express rewrite.
 *
 * Receives SBS batches from the feeder, stores altitude readings in SQLite,
 * and serves a Chart.js altitude graph.
 */

import express, { Request, Response, NextFunction } from "express";
import path from "path";
import os from "os";
import fs from "fs";
import Database from "better-sqlite3";
import { Counter, Histogram, Registry, register } from "prom-client";

import {
  parseSbsLine,
  filterAltitudeOutliers,
  computeRoc,
  computeAglOffset,
  detectEvents,
  findSessionStartQuery,
  AltRow,
} from "./analysis.js";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const DB_PATH = process.env.DB_PATH ?? path.join(__dirname, "../../flighttracker.db");
const STALE_SECONDS = 120;
const MAX_POINTS = 3000;
const VISITOR_TIMEOUT = 60; // seconds

const ALLOWED_IPS = new Set([
  "127.0.0.1",
  "::1",
  "::ffff:127.0.0.1",
  "45.83.241.206",
  "100.111.194.45",
  "80.57.68.254",
]);

const TARGET_AIRCRAFT: Record<string, string> = {
  "484763": "PH-TGC",
  "48484c": "PH-GYS",
  "4849b9": "PH-GOZ",
  "4849a0": "PH-ACX",
  "484ae6": "PH-GBA",
  "4848f9": "PH-RYF",
  "484583": "PH-RIS",
  "48462c": "PH-SKC",
  "48459c": "PH-VHA",
  "4845f0": "PH-VHD",
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
  "48487e": "PH-2X3",
  "484d14": "PH1466",
  "484ff2": "PH-PLP",
  "a8b0a3": "N65909",
  "3ecadc": "D-KRUA",
};

// ---------------------------------------------------------------------------
// Database setup
// ---------------------------------------------------------------------------

const db = new Database(DB_PATH);
db.pragma("journal_mode = WAL");
db.exec(`
  CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    icao_hex  TEXT    NOT NULL,
    ts        TEXT    NOT NULL,
    alt_baro  INTEGER,
    lat       REAL,
    lon       REAL,
    on_ground INTEGER
  );
  CREATE INDEX IF NOT EXISTS idx_readings ON readings(icao_hex, ts);
`);

const stmtInsert = db.prepare(
  "INSERT INTO readings (icao_hex, ts, alt_baro, lat, lon, on_ground) VALUES (?,?,?,?,?,?)",
);

const insertBatch = db.transaction(
  (rows: [string, string, number | null, number | null, number | null, number | null][]) => {
    for (const row of rows) {
      stmtInsert.run(...row);
    }
  },
);

// ---------------------------------------------------------------------------
// In-memory state
// ---------------------------------------------------------------------------

interface AltEntry {
  ts: Date;
  alt: number;
}

const lastSeen = new Map<string, Date>();
const lastAlt = new Map<string, AltEntry>();
let lastPost: Date | null = null;
const visitors = new Map<string, Date>();

// ---------------------------------------------------------------------------
// Prometheus metrics
// ---------------------------------------------------------------------------

const promMessagesReceived = new Counter({
  name: "flighttracker_messages_received_total",
  help: "SBS messages received in POST /sbs batches",
});

const promMessagesParsed = new Counter({
  name: "flighttracker_messages_parsed_total",
  help: "SBS messages successfully parsed and stored",
});

const promMessageLag = new Histogram({
  name: "flighttracker_message_lag_seconds",
  help: "Seconds between message timestamp and server receipt time",
  buckets: [0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600],
});

const promApiRequests = new Counter({
  name: "flighttracker_api_requests_total",
  help: "HTTP requests per endpoint",
  labelNames: ["endpoint"],
});

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

interface IngestResult {
  parsedCount: number;
  aircraftCount: number;
  lags: number[];
}

function ingest(messages: string[]): IngestResult {
  const now = new Date();
  const dbRows: [string, string, number | null, number | null, number | null, number | null][] = [];
  let parsedCount = 0;
  const seenAircraft = new Set<string>();
  const lags: number[] = [];

  for (const line of messages) {
    const parsed = parseSbsLine(line);
    if (!parsed) continue;

    parsedCount++;
    const hexCode = parsed.hex;
    seenAircraft.add(hexCode);
    const msgTs = parsed.ts ? new Date(parsed.ts) : now;
    const altitude = parsed.altitude ?? null;

    lags.push((now.getTime() - msgTs.getTime()) / 1000);

    if (altitude !== null) {
      lastAlt.set(hexCode, { ts: msgTs, alt: altitude });
    }
    lastSeen.set(hexCode, msgTs);

    dbRows.push([
      hexCode,
      msgTs.toISOString(),
      altitude,
      parsed.lat ?? null,
      parsed.lon ?? null,
      parsed.on_ground ?? null,
    ]);
  }

  if (dbRows.length > 0) {
    insertBatch(dbRows);
  }

  return { parsedCount, aircraftCount: seenAircraft.size, lags };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function findSessionStart(icaoHex: string): string | null {
  const { sql, params } = findSessionStartQuery(icaoHex, STALE_SECONDS);
  const row = db.prepare(sql).get(...params) as { session_start: string | null } | undefined;
  return row?.session_start ?? null;
}

function clientIp(req: Request): string {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string") {
    return forwarded.split(",")[0].trim();
  }
  return req.socket.remoteAddress ?? "";
}

function isAllowed(req: Request): boolean {
  return ALLOWED_IPS.has(clientIp(req));
}

// ---------------------------------------------------------------------------
// Express app
// ---------------------------------------------------------------------------

const app = express();
app.set("trust proxy", 1);
app.use(express.json({ limit: "10mb" }));

// Serve static files
app.use(express.static(path.join(__dirname, "../../static")));

// Count API requests
app.use((req: Request, res: Response, next: NextFunction) => {
  res.on("finish", () => {
    const endpoint = req.path;
    if (endpoint !== "/metrics") {
      promApiRequests.labels({ endpoint }).inc();
    }
  });
  next();
});

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

app.post("/sbs", (req: Request, res: Response) => {
  if (!isAllowed(req)) {
    res.status(403).json({ error: "forbidden" });
    return;
  }
  const body = req.body ?? {};
  const messages: string[] = Array.isArray(body.messages) ? body.messages : [];

  lastPost = new Date();

  const { parsedCount, aircraftCount, lags } = ingest(messages);

  promMessagesReceived.inc(messages.length);
  promMessagesParsed.inc(parsedCount);
  for (const lag of lags) {
    promMessageLag.observe(lag);
  }

  let lagInfo = "";
  if (lags.length > 0) {
    const min = Math.min(...lags);
    const avg = lags.reduce((s, v) => s + v, 0) / lags.length;
    const max = Math.max(...lags);
    lagInfo = `  lag min/avg/max: ${min.toFixed(1)}/${avg.toFixed(1)}/${max.toFixed(1)}s`;
  }
  console.log(
    `ingest: ${messages.length} received, ${parsedCount} parsed, ${aircraftCount} aircraft${lagInfo}`,
  );

  res.json({ ok: true, count: messages.length });
});

app.get("/api/aircraft", (_req: Request, res: Response) => {
  const result = Object.entries(TARGET_AIRCRAFT)
    .map(([hex, registration]) => ({ hex, registration }))
    .sort((a, b) => a.registration.localeCompare(b.registration));
  res.json(result);
});

app.get("/api/status", (req: Request, res: Response) => {
  const now = new Date();
  const ip = clientIp(req);
  visitors.set(ip, now);
  const cutoff = now.getTime() - VISITOR_TIMEOUT * 1000;
  const activeUsers = [...visitors.values()].filter((t) => t.getTime() >= cutoff).length;

  const rows = db
    .prepare("SELECT icao_hex, MAX(ts) AS last_seen FROM readings GROUP BY icao_hex")
    .all() as { icao_hex: string; last_seen: string }[];

  const seen: Record<string, string> = {};
  for (const r of rows) seen[r.icao_hex] = r.last_seen;

  const aircraft: Record<string, string | null> = {};
  for (const h of Object.keys(TARGET_AIRCRAFT)) {
    aircraft[h] = seen[h] ?? null;
  }

  res.json({
    last_post: lastPost ? lastPost.toISOString() : null,
    active_users: activeUsers,
    aircraft,
  });
});

app.get("/api/altitude/:hex", (req: Request, res: Response) => {
  const icaoHex = req.params.hex;
  const minutes = parseInt(req.query.minutes as string, 10) || 30;
  const since = new Date(Date.now() - minutes * 60 * 1000).toISOString();

  let rows = db
    .prepare(
      `SELECT ts, alt_baro FROM readings
       WHERE icao_hex = ? AND ts >= ? AND alt_baro IS NOT NULL
       ORDER BY ts`,
    )
    .all(icaoHex, since) as AltRow[];

  rows = filterAltitudeOutliers(rows);
  const aglOffset = computeAglOffset(rows);

  const step = Math.max(1, Math.floor(rows.length / MAX_POINTS));
  rows = rows.filter((_, i) => i % step === 0);

  const roc = computeRoc(rows);

  res.json({
    session_start: findSessionStart(icaoHex),
    points: rows.map((r, i) => ({
      t: r.ts,
      baro: r.alt_baro,
      agl: r.alt_baro - aglOffset,
      roc: roc[i],
    })),
  });
});

app.get("/api/events/:hex", (req: Request, res: Response) => {
  const icaoHex = req.params.hex;
  const todayStart = new Date();
  todayStart.setUTCHours(0, 0, 0, 0);
  const since = todayStart.toISOString();

  let rows = db
    .prepare(
      `SELECT ts, alt_baro FROM readings
       WHERE icao_hex = ? AND ts >= ? AND alt_baro IS NOT NULL
       ORDER BY ts`,
    )
    .all(icaoHex, since) as AltRow[];

  rows = filterAltitudeOutliers(rows);
  const aglOffset = computeAglOffset(rows);

  res.json({
    agl_offset: aglOffset,
    events: detectEvents(rows, aglOffset),
  });
});

app.get("/api/current", (req: Request, res: Response) => {
  const ac = (req.query.ac as string) ?? "PH-TGC";

  // Accept both registration and hex
  let icaoHex = Object.entries(TARGET_AIRCRAFT).find(
    ([, r]) => r.toUpperCase() === ac.toUpperCase(),
  )?.[0];
  if (!icaoHex) {
    icaoHex = ac.toLowerCase() in TARGET_AIRCRAFT ? ac.toLowerCase() : "484763";
  }
  const registration = TARGET_AIRCRAFT[icaoHex] ?? icaoHex;

  const todayStart = new Date();
  todayStart.setUTCHours(0, 0, 0, 0);
  const sinceToday = todayStart.toISOString();

  let rows = db
    .prepare(
      `SELECT ts, alt_baro FROM readings
       WHERE icao_hex = ? AND ts >= ? AND alt_baro IS NOT NULL
       ORDER BY ts`,
    )
    .all(icaoHex, sinceToday) as AltRow[];

  const simple = "simple" in req.query;

  if (rows.length === 0) {
    if (simple) {
      res.set("Content-Type", "text/plain").send("null\n");
    } else {
      res.json({
        registration,
        hex: icaoHex,
        agl: null,
        baro: null,
        ts: null,
        age_secs: null,
      });
    }
    return;
  }

  rows = filterAltitudeOutliers(rows);
  const aglOffset = computeAglOffset(rows);
  const last = rows[rows.length - 1];
  const baro = last.alt_baro;
  const ts = last.ts;
  const ageSecs = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
  const agl = baro - aglOffset;

  if (simple) {
    const value = ageSecs > 60 ? "null" : String(agl);
    res.set("Content-Type", "text/plain").send(value + "\n");
    return;
  }

  res.json({
    registration,
    hex: icaoHex,
    agl,
    baro,
    agl_offset: aglOffset,
    ts,
    age_secs: ageSecs,
  });
});

app.get("/db", (req: Request, res: Response) => {
  if (!isAllowed(req)) {
    res.status(403).json({ error: "forbidden" });
    return;
  }
  const tmpPath = path.join(os.tmpdir(), `flighttracker-backup-${Date.now()}.db`);
  try {
    db.backup(tmpPath).then(() => {
      res.download(tmpPath, "flighttracker.db", { headers: { "Content-Type": "application/octet-stream" } }, (err) => {
        fs.unlink(tmpPath, () => {});
        if (err && !res.headersSent) {
          res.status(500).json({ error: "backup failed" });
        }
      });
    }).catch(() => {
      res.status(500).json({ error: "backup failed" });
    });
  } catch {
    res.status(500).json({ error: "backup failed" });
  }
});

app.get("/metrics", async (_req: Request, res: Response) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

app.get("/robots.txt", (_req: Request, res: Response) => {
  res
    .set("Content-Type", "text/plain")
    .send("User-agent: *\nAllow: /\nSitemap: https://phtgc.nl/sitemap.xml\n");
});

app.get("/sitemap.xml", (_req: Request, res: Response) => {
  const xml =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    "  <url>\n" +
    "    <loc>https://phtgc.nl/</loc>\n" +
    "    <changefreq>daily</changefreq>\n" +
    "    <priority>1.0</priority>\n" +
    "  </url>\n" +
    "</urlset>\n";
  res.set("Content-Type", "application/xml").send(xml);
});

app.get("/", (_req: Request, res: Response) => {
  res.sendFile(path.join(__dirname, "../../templates/index.html"));
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

const PORT = parseInt(process.env.PORT ?? "5000", 10);
app.listen(PORT, "0.0.0.0", () => {
  console.log(`FlightTracker website server`);
  console.log(`Tracking ${Object.keys(TARGET_AIRCRAFT).length} aircraft`);
  console.log(`Listening on http://0.0.0.0:${PORT}`);
});

export default app;
