import { dayKey } from "./format";

export interface DayGroup<T> {
  /** The UTC date ("2026-09-29"). */
  key: string;
  /** The first item's timestamp, for the day's heading. */
  first: string;
  items: T[];
}

/** Consecutive items of a newest-first list, grouped by their UTC day (order kept). */
export function groupByDay<T>(items: readonly T[], at: (item: T) => string): DayGroup<T>[] {
  const days: DayGroup<T>[] = [];
  for (const item of items) {
    const key = dayKey(at(item));
    const day = days.at(-1);
    if (day?.key === key) day.items.push(item);
    else days.push({ key, first: at(item), items: [item] });
  }
  return days;
}
