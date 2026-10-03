import { describe, expect, it } from "vitest";

import { ageLabel, dayHeading, daysAgo, formatDay, formatDayTime, formatDuration, formatTime, isUuid, safeHttpUrl } from "./format";

describe("format", () => {
  it("formats dates in UTC", () => {
    expect(formatDay("2026-09-18T23:30:00Z")).toBe("18 Sep 2026");
    expect(formatDayTime("2026-09-29T09:41:07Z")).toBe("29 Sep 2026, 09:41:07 UTC");
    expect(formatDay(null)).toBeNull();
    expect(formatDay("not a date")).toBeNull();
  });

  it("reads times and ages in UTC calendar days", () => {
    const now = Date.parse("2026-09-29T10:00:00Z");
    expect(formatTime("2026-09-29T08:41:07Z")).toBe("08:41");
    expect(formatTime("2026-09-29T08:41:07Z", { seconds: true })).toBe("08:41:07");
    expect(daysAgo("2026-09-29T00:00:01Z", now)).toBe(0);
    expect(daysAgo("2026-09-28T23:59:59Z", now)).toBe(1);
    expect(ageLabel("2026-09-21T06:00:02Z", now)).toBe("8 days ago");
    expect(ageLabel(null, now)).toBeNull();
    expect(dayHeading("2026-09-29T08:41:07Z", now)).toBe("Today");
    expect(dayHeading("2026-09-28T16:02:55Z", now)).toBe("Yesterday");
    expect(dayHeading("2026-09-16T10:00:00Z", now)).toBe("Wed 16 Sep 2026");
    // A later day than this browser's clock is never called "Today".
    expect(dayHeading("2026-10-02T05:31:14Z", now)).toBe("Fri 2 Oct 2026");
    expect(ageLabel("2026-09-30T01:00:00Z", now)).toBe("today");
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
