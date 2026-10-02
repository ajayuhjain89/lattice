import type { AnswerStatus } from "@/api/types";

/** Short status words for dense lists (Activity, Jump, trace summaries). */
export const SHORT_STATUS: Record<AnswerStatus, string> = {
  answered: "Answered",
  partial: "Partial",
  insufficient_evidence: "No answer",
  evidence_only: "Evidence only",
  ambiguous: "Clarify",
  unsupported_request: "Not supported",
  failed: "Failed",
};

export function shortStatus(status: string): string {
  return SHORT_STATUS[status as AnswerStatus] ?? status;
}
