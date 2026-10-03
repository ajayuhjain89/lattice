"use client";

import { usePathname } from "next/navigation";

import { ViewTabs } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";

/** Administrators switch between their own questions and the organization's runs. */
export function ActivityViews() {
  const { isAdmin } = useWorkspace();
  const pathname = usePathname();
  if (!isAdmin) return null;
  const organization = pathname.startsWith("/activity/organization");
  return (
    <ViewTabs
      label="Activity"
      views={[
        { href: "/activity", label: "Your questions", current: !organization },
        { href: "/activity/organization", label: "Organization runs", current: organization },
      ]}
    />
  );
}
