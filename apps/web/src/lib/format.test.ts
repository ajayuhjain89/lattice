import { describe, expect, it } from "vitest";

import { formatDay, formatDayTime, formatDuration, isUuid, safeHttpUrl } from "./format";

describe("format", () => {
  it("formats dates in UTC", () => {
    expect(formatDay("2026-09-18T23:30:00Z")).toBe("18 Sep 2026");
    expect(formatDayTime("2026-09-29T09:41:07Z")).toBe("29 Sep 2026, 09:41:07 UTC");
    expect(formatDay(null)).toBeNull();
    expect(formatDay("not a date")).toBeNull();
  });

  it("formats durations", () => {
    expect(formatDuration(840)).toBe("840 ms");
    expect(formatDuration(4213)).toBe("4.2 s");
    expect(formatDuration(61_000)).toBe("1 min 1 s");
    expect(formatDuration(null)).toBeNull();
  });

  it("renders only http(s) links", () => {
    expect(safeHttpUrl("https://drive.example/d/1")).toBe("https://drive.example/d/1");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("file:///etc/passwd")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });

  it("recognises UUIDs", () => {
    expect(isUuid("e0000000-0000-4000-8000-000000000001")).toBe(true);
    expect(isUuid("../../etc")).toBe(false);
  });
});
