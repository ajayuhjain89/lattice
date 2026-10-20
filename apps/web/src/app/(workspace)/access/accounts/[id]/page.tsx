import type { Metadata } from "next";

import { AccountScreen } from "@/components/access/AccountScreen";

export const metadata: Metadata = { title: "Account" };

export default async function AccountPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AccountScreen id={id} />;
}
