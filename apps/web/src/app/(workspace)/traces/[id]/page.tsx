import type { Metadata } from "next";

import { TraceScreen } from "@/components/trace/TraceScreen";

export const metadata: Metadata = { title: "Trace" };

export default async function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TraceScreen id={id} />;
}
