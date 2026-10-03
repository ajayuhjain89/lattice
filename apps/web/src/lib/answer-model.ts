/**
 * The view model of an answer, derived once per response and never mutated.
 *
 * Citation numbers are assigned in reading order (direct-answer claims, then supporting
 * claims, then conflict positions) and map 1:1 to evidence items of the same response.
 * The UI counterpart of the API's response invariant: a claim or position that cites
 * anything not available in the response is not rendered at all -- it is never shown
 * with the bad citation stripped -- so a chip can never render for missing evidence.
 */
import type { Citation, Claim, Conflict, EvidenceItem } from "@/api/types";

export interface NumberedCitation extends Citation {
  number: number;
}

export interface RenderClaim {
  key: string;
  text: string;
  role: Claim["role"];
  citations: NumberedCitation[];
}

export interface RenderPosition {
  key: string;
  claim: RenderClaim;
  documentId: string;
  title: string;
  sourceType: string;
  authority: string;
  occurredAt: string | null;
}

export interface RenderConflict {
  key: string;
  kind: Conflict["kind"];
  topic: string | null;
  summary: string;
  positions: RenderPosition[];
  supersession: Conflict["supersession"];
}

export interface AnswerModel {
  lead: RenderClaim[];
  supporting: RenderClaim[];
  conflicts: RenderConflict[];
  /** evidence_id → citation number */
  numberOf: ReadonlyMap<string, number>;
  /** evidence IDs in citation-number order (for previous/next navigation) */
  cited: readonly string[];
  /** evidence_id → the claims that cite it, in reading order */
  citedBy: ReadonlyMap<string, readonly RenderClaim[]>;
  /** claim key → the evidence IDs it cites (statements and conflict positions) */
  cites: ReadonlyMap<string, ReadonlySet<string>>;
  /** Evidence cited by a position of a potential conflict (never a supersession). */
  disputed: ReadonlySet<string>;
  /** How many claims or positions were dropped because a citation was unavailable. */
  dropped: number;
}

interface Source {
  claims: readonly Claim[];
  conflicts: readonly Conflict[];
}

function resolves(claim: Claim, available: ReadonlySet<string>): boolean {
  return claim.citations.length > 0 && claim.citations.every((c) => available.has(c.evidence_id));
}

export function buildAnswerModel(source: Source, available: ReadonlySet<string>): AnswerModel {
  const numberOf = new Map<string, number>();
  const cited: string[] = [];
  const citedBy = new Map<string, RenderClaim[]>();
  const cites = new Map<string, Set<string>>();
  const disputed = new Set<string>();
  let dropped = 0;

  const number = (claim: Claim, key: string): RenderClaim => {
    const rendered: RenderClaim = {
      key,
      text: claim.text,
      role: claim.role,
      citations: claim.citations.map((c) => {
        let n = numberOf.get(c.evidence_id);
        if (n === undefined) {
          n = cited.length + 1;
          numberOf.set(c.evidence_id, n);
          cited.push(c.evidence_id);
        }
        return { ...c, number: n };
      }),
    };
    cites.set(key, new Set(claim.citations.map((c) => c.evidence_id)));
    for (const id of new Set(claim.citations.map((c) => c.evidence_id))) {
      const list = citedBy.get(id) ?? [];
      list.push(rendered);
      citedBy.set(id, list);
    }
    return rendered;
  };

  const keep = (claim: Claim) => {
    if (resolves(claim, available)) return true;
    dropped += 1;
    return false;
  };

  const lead: RenderClaim[] = [];
  const supporting: RenderClaim[] = [];
  source.claims.forEach((claim, i) => {
    if (!keep(claim)) return;
    (claim.role === "direct_answer" ? lead : supporting).push(number(claim, `claim-${i}`));
  });

  const conflicts: RenderConflict[] = [];
  source.conflicts.forEach((conflict, i) => {
    const positions: RenderPosition[] = [];
    conflict.positions.forEach((p, j) => {
      if (!keep(p.claim)) return;
      positions.push({
        key: `conflict-${i}-${j}`,
        claim: number(p.claim, `conflict-${i}-${j}`),
        documentId: p.document_id,
        title: p.title,
        sourceType: p.source_type,
        authority: p.authority,
        occurredAt: p.occurred_at,
      });
    });
    // A conflict needs two positions to mean anything; a supersession needs its link.
    if (conflict.kind === "superseded" ? conflict.supersession !== null : positions.length >= 2) {
      if (conflict.kind === "potential_conflict") for (const p of positions) for (const c of p.claim.citations) disputed.add(c.evidence_id);
      conflicts.push({
        key: `conflict-${i}`,
        kind: conflict.kind,
        topic: conflict.topic,
        summary: conflict.summary,
        positions: sortByDate(positions),
        supersession: conflict.supersession,
      });
    }
  });

  return { lead, supporting, conflicts, numberOf, cited, citedBy, cites, disputed, dropped };
}

/** Oldest first, undated last; stable. Order is chronology, never a verdict. */
function sortByDate(positions: RenderPosition[]): RenderPosition[] {
  return positions
    .map((p, i) => ({ p, i }))
    .sort((a, b) => {
      const x = a.p.occurredAt ? Date.parse(a.p.occurredAt) : Number.POSITIVE_INFINITY;
      const y = b.p.occurredAt ? Date.parse(b.p.occurredAt) : Number.POSITIVE_INFINITY;
      return x === y ? a.i - b.i : x - y;
    })
    .map(({ p }) => p);
}

export interface EvidenceGroups {
  cited: EvidenceItem[];
  /** Readable, but kept out of the prompt by the organization's AI data policy. */
  policyExcluded: EvidenceItem[];
  /** Retrieved, not cited (collapsed by default). */
  other: EvidenceItem[];
}

export function groupEvidence(evidence: readonly EvidenceItem[], model: AnswerModel): EvidenceGroups {
  const cited = evidence
    .filter((e) => model.numberOf.has(e.evidence_id))
    .sort((a, b) => (model.numberOf.get(a.evidence_id) ?? 0) - (model.numberOf.get(b.evidence_id) ?? 0));
  const rest = evidence.filter((e) => !model.numberOf.has(e.evidence_id));
  return {
    cited,
    policyExcluded: rest.filter((e) => e.not_used_reason === "egress_policy"),
    other: rest.filter((e) => e.not_used_reason !== "egress_policy"),
  };
}

/** The first verified quote of an evidence item in this answer, if it is cited. */
export function firstQuote(model: AnswerModel, evidenceId: string): NumberedCitation | null {
  for (const claim of model.citedBy.get(evidenceId) ?? []) {
    const c = claim.citations.find((x) => x.evidence_id === evidenceId);
    if (c) return c;
  }
  return null;
}

/**
 * Where a verified quote sits in a text. Trusts the API's offsets only when they select
 * exactly the quote; otherwise looks for the quote verbatim; otherwise nothing is marked.
 */
export function locateQuote(
  text: string,
  quote: string,
  start?: number,
  end?: number,
): { start: number; end: number } | null {
  if (!quote) return null;
  if (start !== undefined && end !== undefined && text.slice(start, end) === quote) {
    return { start, end };
  }
  const at = text.indexOf(quote);
  return at >= 0 ? { start: at, end: at + quote.length } : null;
}

export type Segment = { text: string; marked: boolean };

/** Split a text into plain and marked segments for the given (possibly overlapping) ranges. */
export function markRanges(text: string, ranges: readonly { start: number; end: number }[]): Segment[] {
  const sorted = [...ranges]
    .filter((r) => r.start < r.end && r.start >= 0 && r.end <= text.length)
    .sort((a, b) => a.start - b.start);
  const merged: { start: number; end: number }[] = [];
  for (const r of sorted) {
    const last = merged.at(-1);
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ ...r });
  }
  const out: Segment[] = [];
  let at = 0;
  for (const r of merged) {
    if (r.start > at) out.push({ text: text.slice(at, r.start), marked: false });
    out.push({ text: text.slice(r.start, r.end), marked: true });
    at = r.end;
  }
  if (at < text.length) out.push({ text: text.slice(at), marked: false });
  return out;
}

/**
 * A short window of an evidence text around its first quote, for list rows: the quote
 * stays visible even when the passage is long.
 */
export function excerpt(
  text: string,
  quote: { start: number; end: number } | null,
  radius = 140,
): { before: string; quote: string; after: string } {
  const clean = (s: string) => s.replace(/\s+/g, " ");
  if (!quote) {
    const body = clean(text).trim();
    return { before: body.length > radius * 2 ? `${body.slice(0, radius * 2).trimEnd()}…` : body, quote: "", after: "" };
  }
  const from = Math.max(0, quote.start - radius / 2);
  const to = Math.min(text.length, quote.end + radius);
  return {
    before: (from > 0 ? "…" : "") + clean(text.slice(from, quote.start)).trimStart(),
    quote: clean(text.slice(quote.start, quote.end)),
    after: clean(text.slice(quote.end, to)).trimEnd() + (to < text.length ? "…" : ""),
  };
}

/** The answer's statements in reading order: direct answers, then supporting claims. */
export function statements(model: AnswerModel): RenderClaim[] {
  return [...model.lead, ...model.supporting];
}

/**
 * The provenance lattice's data (design-direction §5.3): rows are the statements, columns
 * the exhibits they cite, a cell where a statement cites an exhibit. Built only from the
 * answer on screen; conflict positions are not rows (the disagreement axis shows them).
 */
export function provenanceCells(model: AnswerModel): {
  rows: RenderClaim[];
  cols: string[];
  cells: Map<string, "filled">;
} {
  const rows = statements(model);
  const cells = new Map<string, "filled">();
  const used = new Set<string>();
  for (const claim of rows) {
    for (const id of model.cites.get(claim.key) ?? []) {
      cells.set(`${claim.key}|${id}`, "filled");
      used.add(id);
    }
  }
  return { rows, cols: model.cited.filter((id) => used.has(id)), cells };
}

/** A claim shortened at a word boundary, for a lattice row header. */
export function snippet(text: string, max = 44): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.–—-]+$/, "")}…`;
}

/**
 * The passage split into paragraphs (blank lines) with its marked ranges, so the viewer
 * can draw a margin bar beside each paragraph holding a verified quote. Offsets stay those
 * of the original text.
 */
export function paragraphs(text: string, ranges: readonly { start: number; end: number }[]): { segments: Segment[]; cited: boolean }[] {
  const out: { segments: Segment[]; cited: boolean }[] = [];
  const re = /\n[ \t]*\n+/g;
  let at = 0;
  const push = (start: number, end: number) => {
    if (end <= start) return;
    const local = ranges
      .filter((r) => r.end > start && r.start < end)
      .map((r) => ({ start: Math.max(r.start, start) - start, end: Math.min(r.end, end) - start }));
    const segments = markRanges(text.slice(start, end), local);
    out.push({ segments, cited: segments.some((s) => s.marked) });
  };
  for (let m = re.exec(text); m; m = re.exec(text)) {
    push(at, m.index);
    at = m.index + m[0].length;
  }
  push(at, text.length);
  return out;
}
