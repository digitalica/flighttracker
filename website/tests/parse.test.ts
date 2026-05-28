import { describe, it, expect } from "vitest";
import { parseSbsLine } from "../src/analysis";

// Lines that carry no altitude and no position — should return null
describe("parseSbsLine", () => {
  it("type8 squawk change returns null", () => {
    const line =
      "MSG,8,1,1,484763,1,2026/05/09,07:07:30.523,2026/05/09,07:07:30.529,,,,,,,,,,,,";
    expect(parseSbsLine(line)).toBeNull();
  });

  it("type6 surveillance alt returns null", () => {
    const line =
      "MSG,6,1,1,484763,1,2026/05/09,07:12:55.565,2026/05/09,07:12:55.587,PHTGC   ,,,,,,,4250,0,0,0,";
    expect(parseSbsLine(line)).toBeNull();
  });

  it("type7 negative altitude", () => {
    const line =
      "MSG,7,1,1,484763,1,2026/05/09,07:07:31.188,2026/05/09,07:07:31.211,,-200,,,,,,,,,,";
    const result = parseSbsLine(line);
    expect(result).not.toBeNull();
    expect(result!.hex).toBe("484763");
    expect(result!.altitude).toBe(-200);
  });

  it("type5 positive altitude", () => {
    const line =
      "MSG,5,1,1,484763,1,2026/05/09,07:13:05.322,2026/05/09,07:13:05.335,,200,,,,,,,0,,0,";
    const result = parseSbsLine(line);
    expect(result).not.toBeNull();
    expect(result!.hex).toBe("484763");
    expect(result!.altitude).toBe(200);
  });
});
