import type { Metadata } from "next";

import { ProvidersScreen } from "@/components/settings/ProvidersScreen";

export const metadata: Metadata = { title: "AI providers" };

export default function ProvidersPage() {
  return <ProvidersScreen />;
}
