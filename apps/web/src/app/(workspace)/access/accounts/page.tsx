import type { Metadata } from "next";

import { AccountsScreen } from "@/components/access/AccountsScreen";

export const metadata: Metadata = { title: "Accounts" };

export default function AccountsPage() {
  return <AccountsScreen />;
}
