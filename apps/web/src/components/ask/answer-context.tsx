"use client";

import { createContext, useContext } from "react";

import type { EvidenceItem } from "@/api/types";
import type { AnswerModel } from "@/lib/answer-model";

export interface AnswerContextValue {
  model: AnswerModel;
  /** The response's evidence (empty for a stored trace answer, which carries IDs only). */
  items: ReadonlyMap<string, EvidenceItem>;
  open: (evidenceId: string, from: HTMLElement | null) => void;
}

const Context = createContext<AnswerContextValue | null>(null);
export const AnswerContextProvider = Context.Provider;

export function useAnswer(): AnswerContextValue {
  const value = useContext(Context);
  if (!value) throw new Error("useAnswer outside an answer");
  return value;
}
