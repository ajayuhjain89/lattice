import { useState } from "react";

/**
 * The moment this view was opened, read once: ages ("5 days ago") and day headings
 * ("Today") stay stable while the page is open instead of shifting on every render.
 */
export function useNow(): number {
  const [now] = useState(() => Date.now());
  return now;
}
