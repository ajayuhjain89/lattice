/**
 * How each answer status is presented. Every status the API returns has a deliberate
 * treatment; the copy under each heading is the API's own fixed message.
 */
import type { AnswerStatus } from "@/api/types";

export type Tone = "neutral" | "info" | "warning" | "error";

export interface StatusPresentation {
  /** The status word in the brief's status line. */
  label: string;
  tone: Tone;
  /** Heading of the state block for statuses without answer prose. */
  heading: string | null;
  /** The state block's own label: the reason, never a repeat of the status word. */
  reasonLabel: string | null;
}

export function presentStatus(status: AnswerStatus, reason: string | null): StatusPresentation {
  switch (status) {
    case "answered":
      return { label: "Answered", tone: "neutral", heading: null, reasonLabel: null };
    case "partial":
      return { label: "Partial answer", tone: "warning", heading: null, reasonLabel: null };
    case "insufficient_evidence":
      return reason === "claims_unverified"
        ? { label: "Answer withheld", tone: "info", heading: "No verified answer", reasonLabel: "Citation verification" }
        : { label: "No answer", tone: "neutral", heading: "Nothing you can access answers this", reasonLabel: null };
    case "evidence_only":
      return reason === "generation_unavailable"
        ? { label: "Evidence only", tone: "info", heading: "Sources found — no AI model available", reasonLabel: "No AI model" }
        : { label: "Evidence only", tone: "info", heading: "Sources found — not sent to the AI model", reasonLabel: "AI data policy" };
    case "ambiguous":
      return { label: "Needs clarification", tone: "info", heading: null, reasonLabel: null };
    case "unsupported_request":
      return { label: "Not a knowledge question", tone: "neutral", heading: "Lattice doesn't take actions", reasonLabel: null };
    case "failed":
      return {
        label: "Failed",
        tone: "error",
        heading: "No answer was drafted",
        reasonLabel:
          reason === "citation_validation_failed"
            ? "Citation check"
            : reason === "generation_invalid"
              ? "Invalid model response"
              : "AI model unreachable",
      };
  }
}
