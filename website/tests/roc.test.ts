import { describe, it, expect } from "vitest";
import { computeRoc } from "../src/analysis";

function row(offsetSecs: number, alt: number) {
  const base = new Date("2026-01-01T12:00:00.000Z").getTime();
  const ts = new Date(base + offsetSecs * 1000).toISOString();
  return { ts, alt_baro: alt };
}

describe("computeRoc", () => {
  it("empty", () => {
    expect(computeRoc([])).toEqual([]);
  });

  it("single row", () => {
    expect(computeRoc([row(0, 1000)])).toEqual([0]);
  });

  it("steady climb: 600 ft/min = 10 ft/s; points every 5s, window=10s", () => {
    const rows = Array.from({ length: 5 }, (_, i) => row(i * 5, 1000 + i * 50));
    const result = computeRoc(rows, 10);
    expect(result.every((r) => r === 600)).toBe(true);
  });

  it("level flight", () => {
    const rows = Array.from({ length: 5 }, (_, i) => row(i * 5, 2000));
    const result = computeRoc(rows, 10);
    expect(result.every((r) => r === 0)).toBe(true);
  });

  it("descent: -600 ft/min", () => {
    const rows = Array.from({ length: 5 }, (_, i) => row(i * 5, 2000 - i * 50));
    const result = computeRoc(rows, 10);
    expect(result.every((r) => r === -600)).toBe(true);
  });

  it("window limits range", () => {
    // points at 0, 5, 10, 15, 20s; big jump only outside the 10s window
    const rows = [
      row(0, 1000),
      row(5, 1050),
      row(10, 1100),
      row(15, 1100),
      row(20, 1100),
    ];
    const result = computeRoc(rows, 10);
    // middle point (index 2, t=10): lo=0 (t=0 >= 10-10), hi=4 (t=20 <= 10+10)
    // (1100-1000)/20*60 = 300
    expect(result[2]).toBe(300);
    // last point (index 4, t=20): lo clips to index 2 (t=10), hi=4
    // (1100-1100)/10*60 = 0
    expect(result[4]).toBe(0);
  });
});
