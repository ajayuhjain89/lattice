/**
 * Presentation of the organization's setup record (phase-12-plan C12). The API derives
 * every state and reason and writes every message; this file only names categories,
 * gives each state its word and icon, and sets the API's facts as a ledger line. It never
 * decides whether something is complete.
 */
import { AlertTriangle, CheckCircle2, CircleDashed, CircleMinus, Clock, Lock, type LucideIcon } from "lucide-react";

import type { SetupCategory, SetupCategoryKey, SetupState } from "@/api/types";
import type { StatusTone } from "@/components/primitives/StatusMark";
import { formatCount, formatDayTime, humanize, levelLabel } from "@/lib/format";

export const CATEGORY_LABEL: Record<SetupCategoryKey, string> = {
  organization: "Organization",
  identity: "Identity provider",
  members: "Members",
  provisioning: "Provisioning (SCIM)",
  ai_policy: "AI policy",
  sources: "Sources",
  accounts: "Account mapping",
  first_answer: "First answer",
};

interface StatePresentation {
  word: string;
  tone: StatusTone;
  icon: LucideIcon;
}

/** One signal: only a completed item is verdigris and a running one ochre; the rest are ink. */
export const STATE: Record<SetupState, StatePresentation> = {
  complete: { word: "Complete", tone: "healthy", icon: CheckCircle2 },
  in_progress: { word: "In progress", tone: "time", icon: Clock },
  attention: { word: "Needs attention", tone: "neutral", icon: AlertTriangle },
  required: { word: "Required", tone: "neutral", icon: CircleDashed },
  blocked: { word: "Blocked", tone: "neutral", icon: Lock },
  optional: { word: "Optional", tone: "neutral", icon: CircleMinus },
};

/**
 * States that ask someone to act now. Without a link (the API's ``action`` is null only
 * when no page in the product owns the item yet) that someone is the Lattice operator. A
 * blocked item waits on another one, which its message names.
 */
export const ACTIONABLE: ReadonlySet<SetupState> = new Set(["required", "attention"]);

type Facts = SetupCategory["facts"];

const num = (facts: Facts, key: string): number | null => {
  const value = facts[key];
  return typeof value === "number" ? value : null;
};

const str = (facts: Facts, key: string): string | null => {
  const value = facts[key];
  return typeof value === "string" && value ? value : null;
};

function counted(facts: Facts, key: string, one: string, many: string, { zero = true } = {}): string | null {
  const n = num(facts, key);
  if (n === null || (n === 0 && !zero)) return null;
  return formatCount(n, one, many);
}

const CONNECTION: Record<string, string> = {
  active: "Connection active",
  disabled: "Connection disabled",
  none: "No connection",
};

/** The API's facts as ledger fragments (counts, times, names it returned). */
export function factLine(category: SetupCategory): string[] {
  const f = category.facts;
  const parts: (string | null)[] = [];
  switch (category.key) {
    case "organization":
      parts.push(counted(f, "active_administrators", "active administrator", "active administrators"));
      break;
    case "identity": {
      const connection = str(f, "connection");
      parts.push(connection ? (CONNECTION[connection] ?? humanize(connection)) : null);
      const success = formatDayTime(str(f, "last_success_at"));
      const failure = formatDayTime(str(f, "last_failure_at"));
      parts.push(success ? `Last sign-in ${success}` : null, failure ? `Last failure ${failure}` : null);
      break;
    }
    case "members":
      parts.push(
        counted(f, "active_members", "active member", "active members"),
        counted(f, "active_administrators", "administrator", "administrators"),
      );
      break;
    case "provisioning":
      parts.push(
        counted(f, "live_tokens", "live token", "live tokens"),
        counted(f, "provisioned_members", "provisioned member", "provisioned members", { zero: false }),
      );
      break;
    case "ai_policy": {
      const provider = str(f, "provider");
      const model = str(f, "model");
      parts.push(provider ? [provider, model].filter(Boolean).join(" · ") : null);
      const level = str(f, "default_question_sensitivity");
      parts.push(level ? `Questions default to ${levelLabel(level)}` : null);
      break;
    }
    case "sources":
      parts.push(
        counted(f, "connected_sources", "source", "sources"),
        counted(f, "synced_sources", "synced", "synced", { zero: false }),
        counted(f, "failed_sources", "failed", "failed", { zero: false }),
        counted(f, "syncing_sources", "syncing", "syncing", { zero: false }),
      );
      break;
    case "accounts":
      parts.push(
        counted(f, "account_sources", "account-scoped source", "account-scoped sources", { zero: false }),
        counted(f, "unmapped_references", "unmapped reference", "unmapped references", { zero: false }),
      );
      break;
    case "first_answer":
      break;
  }
  return parts.filter((p): p is string => p !== null);
}
