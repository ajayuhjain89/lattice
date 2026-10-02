import type { Metadata } from "next";

import { MemberScreen } from "@/components/access/MemberScreen";

export const metadata: Metadata = { title: "Member access" };

export default async function MemberAccessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <MemberScreen id={id} />;
}
