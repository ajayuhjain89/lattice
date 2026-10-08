import type { Metadata } from "next";

import { OrganizationScreen } from "@/components/settings/OrganizationScreen";

export const metadata: Metadata = { title: "Organization" };

export default function OrganizationPage() {
  return <OrganizationScreen />;
}
