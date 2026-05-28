import { describe, it, expect } from "vitest";
import { computeAglOffset } from "../src/analysis";

function rows(...alts: number[]) {
  return alts.map((a) => ({ alt_baro: a }));
}

describe("computeAglOffset", () => {
  it("example positive: two lowest are 100 and 200; avg of (100,200,200,200) rounds to 200", () => {
    expect(computeAglOffset(rows(100, 200, 200, 200))).toBe(200);
  });

  it("example negative: two lowest are -300 and -200; avg rounds to -200", () => {
    expect(computeAglOffset(rows(-200, -200, -300, -200, -200))).toBe(-200);
  });

  it("single row", () => {
    expect(computeAglOffset(rows(150))).toBe(200);
  });

  it("empty returns 0", () => {
    expect(computeAglOffset([])).toBe(0);
  });

  it("all same", () => {
    expect(computeAglOffset(rows(200, 200, 200))).toBe(200);
  });

  it("rounds to nearest 100: two lowest 50 and 100; avg=75 rounds to 100", () => {
    expect(computeAglOffset(rows(50, 100, 500, 500))).toBe(100);
  });

  it("ignores altitudes at or above 1000", () => {
    expect(computeAglOffset(rows(200, 1000, 2000, 5000))).toBe(200);
  });

  it("all above 1000 returns zero", () => {
    expect(computeAglOffset(rows(1000, 2000, 5000))).toBe(0);
  });

  it("high altitude flight ignored; values below 1000 ft win", () => {
    expect(computeAglOffset(rows(100, 200, 3000, 6000, 8000))).toBe(200);
  });
});
