/**
 * Lattice's own glyphs (design-direction §4.5): the product's concepts, drawn on Lucide's
 * 16-unit grid at the same 1.5 stroke so they sit beside its icons. Use them through
 * `<Icon as={…} />`, which makes every icon decorative and sized consistently.
 */
import type { SVGProps } from "react";

export interface GlyphProps extends Omit<SVGProps<SVGSVGElement>, "ref"> {
  size?: number;
  strokeWidth?: number;
}

export type Glyph = ((props: GlyphProps) => React.JSX.Element) & { displayName: string };

function glyph(name: string, body: (stroke: number) => React.JSX.Element): Glyph {
  const G = ({ size = 16, strokeWidth = 1.5, ...rest }: GlyphProps) => (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {body(strokeWidth)}
    </svg>
  );
  G.displayName = name;
  return G;
}

/** A numbered exhibit: the squared marker that ties a claim to its source. */
export const ExhibitGlyph = glyph("ExhibitGlyph", () => (
  <>
    <rect x="2.75" y="2.75" width="10.5" height="10.5" rx="1" />
    <path d="M6.75 6.25 8 5.25v5.5M6.5 10.75h3" />
  </>
));

/** A verified passage: lines of text, one marked. */
export const PassageGlyph = glyph("PassageGlyph", () => (
  <>
    <path d="M2.5 3.5h11M2.5 12.5h7" />
    <rect x="2" y="6.25" width="12" height="3.5" rx="0.75" fill="currentColor" fillOpacity="0.22" stroke="none" />
    <path d="M2.5 8h11" />
  </>
));

/** A boundary: a hatched square -- "not crossed". */
export const BoundaryGlyph = glyph("BoundaryGlyph", () => (
  <>
    <rect x="2.75" y="2.75" width="10.5" height="10.5" rx="1" strokeDasharray="2 1.5" />
    <path d="M2.75 8.25 8.25 2.75M2.75 13.25 13.25 2.75M7.75 13.25l5.5-5.5" strokeWidth="1" />
  </>
));

/** Egress: an arrow crossing a boundary edge (what may leave Lattice). */
export const EgressGlyph = glyph("EgressGlyph", () => (
  <>
    <path d="M9 1.75v12.5" strokeDasharray="1.5 1.75" strokeWidth="1.25" />
    <path d="M2.25 8h11M10.5 5.25 13.25 8l-2.75 2.75" />
  </>
));

/** A provenance trail: linked steps ending in the verified one. */
export const TrailGlyph = glyph("TrailGlyph", () => (
  <>
    <circle cx="3" cy="8" r="1.75" />
    <circle cx="8" cy="8" r="1.75" />
    <circle cx="13" cy="8" r="1.75" fill="currentColor" />
    <path d="M4.75 8h1.5M9.75 8h1.5" />
  </>
));

/** The lattice: rows by columns of real relationships (the mark, in one colour). */
export const LatticeGlyph = glyph("LatticeGlyph", () => (
  <>
    <rect x="1.75" y="1.75" width="12.5" height="12.5" rx="1" />
    <path d="M5.92 1.75v12.5M10.08 1.75v12.5M1.75 5.92h12.5M1.75 10.08h12.5" />
    <rect x="5.92" y="5.92" width="4.16" height="4.16" fill="currentColor" stroke="none" />
  </>
));

/** Source type: a document (folded page). */
export const DocumentGlyph = glyph("DocumentGlyph", () => (
  <>
    <path d="M3.75 1.75h5.5l3 3v9.5h-8.5z" />
    <path d="M9.25 1.75v3h3M6 8.25h4M6 11h4" />
  </>
));

/** Source type: a ticket (notched stub). */
export const TicketGlyph = glyph("TicketGlyph", () => (
  <>
    <path d="M1.75 4.25h12.5v2.25a1.5 1.5 0 0 0 0 3v2.25H1.75V9.5a1.5 1.5 0 0 0 0-3z" />
    <path d="M10.25 4.75v6.5" strokeDasharray="1.25 1.5" strokeWidth="1.25" />
  </>
));

/** Source type: chat (a message in a thread). */
export const ChatGlyph = glyph("ChatGlyph", () => (
  <>
    <path d="M2.25 2.75h11.5v8H7.5L4.25 13.5v-2.75h-2z" />
    <path d="M5 6.75h6" />
  </>
));

export const LATTICE_GLYPHS = {
  exhibit: ExhibitGlyph,
  passage: PassageGlyph,
  boundary: BoundaryGlyph,
  egress: EgressGlyph,
  trail: TrailGlyph,
  lattice: LatticeGlyph,
} as const;

export const SOURCE_GLYPHS = { doc: DocumentGlyph, ticket: TicketGlyph, chat: ChatGlyph } as const;
