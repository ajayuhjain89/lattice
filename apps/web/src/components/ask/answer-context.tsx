"use client";

import { createContext, useContext } from "react";

import type { EvidenceItem } from "@/api/types";
import type { AnswerModel } from "@/lib/answer-model";
import { useStore, type Store } from "@/lib/store";

/**
 * What is being pointed at in the answer right now: a statement (its markers hovered or
 * focused), an exhibit (its row, marker or lattice column), or one relationship (a lattice
 * cell, or one marker of one statement). Everything linked to it lights together, so the
 * relationship reads the same from either end -- by mouse or by keyboard focus.
 */
export interface Pointer {
  claim: string | null;
  evidence: string | null;
}

export const NO_POINTER: Pointer = { claim: null, evidence: null };

export interface AnswerContextValue {
  model: AnswerModel;
  /** The response's evidence (empty for a stored trace answer, which carries IDs only). */
  items: ReadonlyMap<string, EvidenceItem>;
  open: (evidenceId: string, from: HTMLElement | null, claim?: string | null) => void;
  pointer: Store<Pointer>;
}

const Context = createContext<AnswerContextValue | null>(null);
export const AnswerContextProvider = Context.Provider;

export function useAnswer(): AnswerContextValue {
  const value = useContext(Context);
  if (!value) throw new Error("useAnswer outside an answer");
  return value;
}

/** A statement is lit when it is pointed at, or cites the exhibit pointed at. */
export function useClaimLit(key: string): boolean {
  const { pointer, model } = useAnswer();
  return useStore(pointer, (p) => p.claim === key || (p.claim === null && p.evidence !== null && Boolean(model.cites.get(key)?.has(p.evidence))));
}

/** An exhibit is lit when it is pointed at, or the statement pointed at cites it. */
export function useExhibitLit(evidenceId: string): boolean {
  const { pointer, model } = useAnswer();
  return useStore(pointer, (p) => p.evidence === evidenceId || (p.evidence === null && p.claim !== null && Boolean(model.cites.get(p.claim)?.has(evidenceId))));
}

/** Handlers that point at something while it is hovered or focused. */
export function pointerHandlers(pointer: Store<Pointer>, target: Pointer) {
  const on = () => pointer.set((p) => (p.claim === target.claim && p.evidence === target.evidence ? p : target));
  const off = () => pointer.set((p) => (p.claim === target.claim && p.evidence === target.evidence ? NO_POINTER : p));
  return { onMouseEnter: on, onMouseLeave: off, onFocus: on, onBlur: off };
}

/** Stable DOM id of one statement's marker for one exhibit (the inspector links back to it). */
export function markerId(claimKey: string, number: number): string {
  return `cite-${claimKey}-${number}`;
}
