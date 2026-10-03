import { PauseCircle, UserX } from "lucide-react";

import type { MemberSummary } from "@/api/types";
import { StatusMark } from "@/components/primitives/StatusMark";

/**
 * A member's lifecycle in words: active needs no mark; suspended and removed carry an
 * icon and their word (never colour alone, never alarm red: they are states, not faults).
 */
export function MemberStatus({ status }: { status: MemberSummary["status"] }) {
  if (status === "suspended") {
    return (
      <StatusMark tone="neutral" icon={PauseCircle}>
        Suspended
      </StatusMark>
    );
  }
  if (status === "removed") {
    return (
      <StatusMark tone="neutral" icon={UserX}>
        Removed
      </StatusMark>
    );
  }
  return <span>Active</span>;
}

export function accountsSummary(m: Pick<MemberSummary, "account_scope" | "assigned_accounts">): string {
  if (m.account_scope === "all") return "All accounts";
  return m.assigned_accounts === 0 ? "None assigned" : `${m.assigned_accounts.toLocaleString("en-GB")} assigned`;
}

export const managedByIdentityProvider = (m: Pick<MemberSummary, "managed_by">) => m.managed_by === "identity_provider";
