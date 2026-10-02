/**
 * State of one signed-in tab, in memory only (never persisted): the current Ask, the
 * evidence open in the inspector, and the composer's text. Signing out drops all of it.
 * Past questions are not kept here: they come from GET /v1/activity, which persists them
 * and re-authorizes every trace they link to.
 */
import type { Answer, AskRequest, EvidenceItem } from "@/api/types";
import type { ApiError } from "@/api/errors";

import type { RenderClaim } from "./answer-model";

import { createStore, type Store } from "./store";

export interface AskState {
  /** The request that produced `answer` or `error` (retry resends exactly this). */
  request: AskRequest | null;
  /** Display name of `request.scope.account`, known only from the clarification chosen. */
  accountName: string | null;
  answer: Answer | null;
  error: ApiError | null;
  pending: boolean;
  startedAt: number | null;
  /** Round-trip time of the last request, measured by this tab. */
  elapsedMs?: number;
}

/** What the page that opened the inspector knows about the evidence's role in its answer. */
export interface InspectorContext {
  /** evidence_id → citation number in the answer on screen */
  numberOf: ReadonlyMap<string, number>;
  /** evidence_id → the claims of the answer on screen that cite it */
  citedBy: ReadonlyMap<string, readonly RenderClaim[]>;
  /** Evidence IDs in reading order, for previous/next ([ and ]). */
  order: readonly string[];
  /** The answer's own evidence item, for why it was or wasn't used (never for content). */
  items: ReadonlyMap<string, EvidenceItem>;
}

export interface InspectorState {
  evidenceId: string | null;
  /** The element to return focus to when the inspector closes. */
  returnFocus: HTMLElement | null;
  context: InspectorContext | null;
}

export interface WorkspaceState {
  ask: Store<AskState>;
  inspector: Store<InspectorState>;
  /** The composer's current text, kept so a sign-in redirect can preserve it. */
  draft: Store<string>;
  /** A question chosen in Jump ("Ask “…”"): Ask puts it in the composer, then clears it. */
  prefill: Store<string | null>;
}

export const EMPTY_ASK: AskState = {
  request: null,
  accountName: null,
  answer: null,
  error: null,
  pending: false,
  startedAt: null,
};
export const CLOSED_INSPECTOR: InspectorState = { evidenceId: null, returnFocus: null, context: null };

export function createWorkspaceState(): WorkspaceState {
  return {
    ask: createStore<AskState>(EMPTY_ASK),
    inspector: createStore<InspectorState>(CLOSED_INSPECTOR),
    draft: createStore(""),
    prefill: createStore<string | null>(null),
  };
}

/** The draft kept across a sign-in redirect (the person's own text; this tab only). */
export const DRAFT_KEY = "lattice.draft-question";

export function saveDraft(text: string): void {
  try {
    if (text.trim()) sessionStorage.setItem(DRAFT_KEY, text);
  } catch {
    /* storage unavailable: the draft is simply not kept */
  }
}

export function takeDraft(): string | null {
  try {
    const text = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    return text;
  } catch {
    return null;
  }
}
