import { describe, expect, it } from "vitest";
import { formatClock, formatDateTime, formatDuration, formatMs } from "./format";

describe("formatting", () => {
  it("shows a call's length in minutes and seconds", () => {
    expect(formatDuration(83)).toBe("1m 23s");
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(120)).toBe("2m 0s");
    expect(formatDuration(null)).toBe("Unknown");
  });

  it("shows a position in the call as m:ss", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(65.9)).toBe("1:05");
    expect(formatClock(-3)).toBe("0:00");
  });

  it("rounds milliseconds to whole numbers and says when there is none", () => {
    expect(formatMs(419.6)).toBe("420 ms");
    expect(formatMs(null)).toBe("n/a");
  });

  it("shows London time whatever the viewer's own zone is", () => {
    // 12:30 UTC in late June is 13:30 in London (BST); in January the two are the same.
    expect(formatDateTime("2026-06-30T12:30:00Z")).toContain("13:30");
    expect(formatDateTime("2026-01-15T12:30:00Z")).toContain("12:30");
  });

  it("does not throw on a missing or unreadable timestamp", () => {
    expect(formatDateTime(null)).toBe("Unknown");
    expect(formatDateTime("not a date")).toBe("Unknown");
  });
});
