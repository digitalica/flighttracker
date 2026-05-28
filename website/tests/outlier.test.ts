import { describe, it, expect } from "vitest";
import { filterAltitudeOutliers } from "../src/analysis";

function row(offsetSecs: number, alt: number) {
  const base = new Date("2026-01-01T12:00:00.000Z").getTime();
  const ts = new Date(base + offsetSecs * 1000).toISOString();
  return { ts, alt_baro: alt };
}

function rowMs(offsetMs: number, alt: number) {
  const base = new Date("2026-01-01T12:00:00.000Z").getTime();
  const ts = new Date(base + offsetMs).toISOString();
  return { ts, alt_baro: alt };
}

describe("filterAltitudeOutliers", () => {
  it("no outliers unchanged (~600 ft/min climb, well within limit)", () => {
    const rows = Array.from({ length: 5 }, (_, i) => row(i * 10, 1000 + i * 100));
    expect(filterAltitudeOutliers(rows)).toEqual(rows);
  });

  it("interior spike removed (50000 ft vs both neighbors in 10s)", () => {
    const rows = [row(0, 1000), row(10, 1010), row(20, 50000), row(30, 1020), row(40, 1030)];
    const result = filterAltitudeOutliers(rows);
    expect(result).toHaveLength(4);
    expect(result.every((r) => r.alt_baro !== 50000)).toBe(true);
  });

  it("neighbors of spike not removed", () => {
    const rows = [row(0, 1000), row(10, 1010), row(20, 50000), row(30, 1020), row(40, 1030)];
    const result = filterAltitudeOutliers(rows);
    const alts = result.map((r) => r.alt_baro);
    expect(alts).toContain(1010);
    expect(alts).toContain(1020);
  });

  it("first point outlier removed", () => {
    const rows = [row(0, 50000), row(10, 1000), row(20, 1010)];
    const result = filterAltitudeOutliers(rows);
    expect(result).toHaveLength(2);
    expect(result[0].alt_baro).toBe(1000);
  });

  it("last point outlier removed", () => {
    const rows = [row(0, 1000), row(10, 1010), row(20, 50000)];
    const result = filterAltitudeOutliers(rows);
    expect(result).toHaveLength(2);
    expect(result[result.length - 1].alt_baro).toBe(1010);
  });

  it("single row unchanged", () => {
    const rows = [row(0, 1000)];
    expect(filterAltitudeOutliers(rows)).toEqual(rows);
  });

  it("empty unchanged", () => {
    expect(filterAltitudeOutliers([])).toEqual([]);
  });

  it("spike with sub-second neighbors is removed", () => {
    // Spike at 38.994s is only 4ms after previous and 796ms before next
    const rows = [
      rowMs(38_965, 2300),
      rowMs(38_990, 2300),
      rowMs(38_994, 11325), // spike
      rowMs(39_790, 2300),
      rowMs(41_858, 2300),
    ];
    const result = filterAltitudeOutliers(rows);
    expect(result).toHaveLength(4);
    expect(result.every((r) => r.alt_baro !== 11325)).toBe(true);
  });

  it("spike at end of data followed by 31-minute gap is filtered", () => {
    // The spike has only one neighbor (previous point, nearby).
    // The other neighbor is 31 minutes away — exceeds STALE_SECONDS (120s),
    // so it is ignored. The one valid neighbor exceeds the rate, so spike is removed.
    const rows = [
      row(0, 1000),
      row(10, 1010),
      row(20, 50000),         // spike — only left neighbor is valid (10s gap)
      row(20 + 31 * 60, 1020), // 31 min later — gap neighbor, should be ignored
    ];
    const result = filterAltitudeOutliers(rows);
    const alts = result.map((r) => r.alt_baro);
    expect(alts).not.toContain(50000);
  });
});
