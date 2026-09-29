import type { AskRequest, Level, SourceType } from "@/api/types";

/** What the composer edits. Converted to an AskRequest only at submit. */
export interface AskForm {
  question: string;
  sourceTypes: SourceType[];
  since: string; // yyyy-mm-dd or ""
  until: string;
  /** Can only raise the question's sensitivity above the organization default (D-13). */
  classification: Exclude<Level, "internal"> | null;
  account: { key: string; name: string } | null;
}

export const MAX_QUESTION = 2000;

export const EMPTY_FORM: AskForm = {
  question: "",
  sourceTypes: [],
  since: "",
  until: "",
  classification: null,
  account: null,
};

export function toRequest(form: AskForm): AskRequest {
  const scope: AskRequest["scope"] = {};
  if (form.account) scope.account = form.account.key;
  if (form.sourceTypes.length) scope.source_types = [...form.sourceTypes];
  if (form.since) scope.since = form.since;
  if (form.until) scope.until = form.until;
  const request: AskRequest = { question: form.question.trim(), scope };
  if (form.classification) request.classification = form.classification;
  return request;
}

export function fromRequest(request: AskRequest, accountName: string | null): AskForm {
  const scope = request.scope ?? {};
  const classification = request.classification === "confidential" || request.classification === "restricted" ? request.classification : null;
  return {
    question: request.question,
    sourceTypes: [...(scope.source_types ?? [])],
    since: scope.since ?? "",
    until: scope.until ?? "",
    classification,
    account: scope.account ? { key: scope.account, name: accountName ?? scope.account } : null,
  };
}

export type FormProblem = "empty" | "too_long" | "dates";

export function validate(form: AskForm): FormProblem | null {
  const q = form.question.trim();
  if (!q) return "empty";
  if (q.length > MAX_QUESTION) return "too_long";
  if (form.since && form.until && form.since > form.until) return "dates";
  return null;
}
