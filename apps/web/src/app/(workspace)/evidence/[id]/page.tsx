import type { Metadata } from "next";

import { EvidenceScreen } from "@/components/evidence/EvidenceScreen";

export const metadata: Metadata = { title: "Evidence" };

export default async function EvidencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <EvidenceScreen id={id} />;
}
