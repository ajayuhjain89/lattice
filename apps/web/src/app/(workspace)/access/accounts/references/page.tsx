import type { Metadata } from "next";

import { ReferencesScreen } from "@/components/access/ReferencesScreen";

export const metadata: Metadata = { title: "Unmapped references" };

export default function ReferencesPage() {
  return <ReferencesScreen />;
}
