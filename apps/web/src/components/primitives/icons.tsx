/**
 * The icon set: Lucide for general UI and Lattice's own glyphs (./glyphs) for product
 * concepts, both on a 16 px grid with a 1.5 px stroke in currentColor. Every icon is
 * decorative (aria-hidden) and always paired with text or an accessible name.
 */
import type { LucideIcon, LucideProps } from "lucide-react";

import type { Glyph, GlyphProps } from "./glyphs";

export function Icon({ as: Shape, size = 16, ...rest }: { as: LucideIcon | Glyph } & LucideProps & GlyphProps) {
  return <Shape size={size} strokeWidth={1.5} aria-hidden="true" focusable="false" {...rest} />;
}
