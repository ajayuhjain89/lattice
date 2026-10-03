"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Whether an element currently scrolls horizontally. A region that scrolls must be
 * reachable by keyboard (WCAG 2.1.1): callers make it a focusable, named region only
 * while it overflows, so a table that fits adds no extra Tab stop.
 */
export function useOverflow<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setOverflows(el.scrollWidth > el.clientWidth + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    for (const child of Array.from(el.children)) observer.observe(child);
    return () => observer.disconnect();
  }, []);
  return [ref, overflows] as const;
}

/** Props for a scroll container that is a keyboard-scrollable region only when it scrolls. */
export function scrollRegionProps(overflows: boolean, label: string) {
  return overflows ? { tabIndex: 0, role: "region" as const, "aria-label": label } : {};
}
