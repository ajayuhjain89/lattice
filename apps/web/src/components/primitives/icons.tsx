/**
 * The icon set (Lucide, one family): 16 px, 1.5 px stroke, currentColor. Every icon is
 * decorative (aria-hidden) and always paired with text or an accessible name.
 */
import type { LucideIcon, LucideProps } from "lucide-react";

export function Icon({ as: Glyph, size = 16, ...rest }: { as: LucideIcon } & LucideProps) {
  return <Glyph size={size} strokeWidth={1.5} aria-hidden="true" focusable="false" {...rest} />;
}
