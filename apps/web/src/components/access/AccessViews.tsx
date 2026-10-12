"use client";

import { usePathname } from "next/navigation";

import { ViewTabs } from "@/components/ops/OpsPage";

/** Access's views: the members (and each member's page), the accounts (P12.3: each
 *  account's record and the unmapped references) and the audit log. */
export function AccessViews() {
  const pathname = usePathname();
  const audit = pathname.startsWith("/access/audit");
  const accounts = pathname.startsWith("/access/accounts");
  return (
    <ViewTabs
      label="Access"
      views={[
        { href: "/access", label: "Members", current: !audit && !accounts },
        { href: "/access/accounts", label: "Accounts", current: accounts },
        { href: "/access/audit", label: "Audit log", current: audit },
      ]}
    />
  );
}
