import type { Metadata } from "next";

import { AuditScreen } from "@/components/access/AuditScreen";

export const metadata: Metadata = { title: "Audit log" };

export default function AuditPage() {
  return <AuditScreen />;
}
