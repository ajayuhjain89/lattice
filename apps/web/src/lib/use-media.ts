import { useSyncExternalStore } from "react";

/** Breakpoints of the layout grid (design-system §B6). */
export const BREAKPOINTS = { mobile: 768, tablet: 1024, docked: 1280, wide: 1440 } as const;

export function useMinWidth(px: number): boolean {
  const query = `(min-width: ${px}px)`;
  return useSyncExternalStore(
    (notify) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => window.matchMedia(query).matches,
    () => true,
  );
}
