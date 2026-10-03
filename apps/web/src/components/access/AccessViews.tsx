"use client";

import { usePathname } from "next/navigation";

import { ViewTabs } from "@/components/ops/OpsPage";

/** Access's two views: the members (and each member's page) and the audit log. */
export function AccessViews() {
  const pathname = usePathname();
  const audit = pathname.startsWith("/access/audit");
  return (
    <ViewTabs
      label="Access"
      views={[
        { href: "/access", label: "Members", current: !audit },
        { href: "/access/audit", label: "Audit log", current: audit },
      ]}
    />
  );
}
