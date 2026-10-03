/**
 * Every colour pairing the system relies on, with the WCAG 2.2 minimum it must meet:
 * 4.5:1 for text (1.4.3), 3:1 for control boundaries and meaningful graphics (1.4.11).
 * Checked for both theme maps by tokens.test.ts and shown live on /specimen.
 */
export interface ContrastPair {
  fg: string;
  bg: string;
  min: 4.5 | 3;
  use: string;
}

const SURFACES = ["--surface-paper", "--surface-sheet", "--surface-well", "--surface-well-strong"];
const TEXT = ["--text-primary", "--text-secondary", "--text-muted"];
const SIGNALS = ["provenance", "disagreement", "time", "fault", "healthy"] as const;

export const CONTRAST_PAIRS: readonly ContrastPair[] = [
  ...TEXT.flatMap((fg) => SURFACES.map((bg) => ({ fg, bg, min: 4.5 as const, use: "text on a surface" }))),
  { fg: "--text-inverse", bg: "--surface-inverse", min: 4.5, use: "primary button, tooltip" },
  { fg: "--text-inverse", bg: "--surface-inverse-hover", min: 4.5, use: "primary button hover" },
  { fg: "--text-inverse", bg: "--surface-inverse-active", min: 4.5, use: "primary button pressed" },
  ...SIGNALS.flatMap((s) => [
    { fg: `--signal-${s}-text`, bg: "--surface-paper", min: 4.5 as const, use: `${s} text on paper` },
    { fg: `--signal-${s}-text`, bg: "--surface-sheet", min: 4.5 as const, use: `${s} text on sheet` },
    { fg: `--signal-${s}-text`, bg: `--signal-${s}-tint`, min: 4.5 as const, use: `${s} text on its tint` },
    { fg: "--text-primary", bg: `--signal-${s}-tint`, min: 4.5 as const, use: `body text in a ${s} region` },
    { fg: "--text-secondary", bg: `--signal-${s}-tint`, min: 4.5 as const, use: `references in a ${s} region` },
    { fg: `--signal-${s}-mark`, bg: "--surface-paper", min: 3 as const, use: `${s} rule or marker on paper` },
    { fg: `--signal-${s}-mark`, bg: "--surface-sheet", min: 3 as const, use: `${s} rule or marker on sheet` },
  ]),
  { fg: "--signal-provenance-text", bg: "--surface-well", min: 4.5, use: "a link on a hovered row" },
  { fg: "--signal-passage-text", bg: "--signal-passage-fill", min: 4.5, use: "selected exhibit marker" },
  { fg: "--signal-passage-text", bg: "--signal-passage-tint", min: 4.5, use: "a verified quote" },
  { fg: "--rule-strong", bg: "--surface-paper", min: 3, use: "control boundary on paper" },
  { fg: "--rule-strong", bg: "--surface-sheet", min: 3, use: "control boundary on sheet" },
  { fg: "--rule-strong", bg: "--surface-well", min: 3, use: "control boundary on well" },
  { fg: "--boundary-edge", bg: "--surface-paper", min: 3, use: "boundary edge" },
  { fg: "--rule-ink", bg: "--surface-paper", min: 3, use: "authority rule" },
  { fg: "--focus-ring", bg: "--surface-paper", min: 3, use: "focus ring on paper" },
  { fg: "--focus-ring", bg: "--surface-sheet", min: 3, use: "focus ring on sheet" },
  { fg: "--focus-ring", bg: "--surface-well", min: 3, use: "focus ring on well" },
];

/** WCAG relative luminance of an sRGB colour given as 0–255 channels. */
export function luminance([r, g, b]: readonly [number, number, number]): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrast(a: readonly [number, number, number], b: readonly [number, number, number]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}
