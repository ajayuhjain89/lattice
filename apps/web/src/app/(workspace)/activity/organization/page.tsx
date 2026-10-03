import type { Metadata } from "next";

import { OrganizationActivityScreen } from "@/components/activity/OrganizationActivityScreen";

export const metadata: Metadata = { title: "Organization runs" };

export default function OrganizationActivityPage() {
  return <OrganizationActivityScreen />;
}
