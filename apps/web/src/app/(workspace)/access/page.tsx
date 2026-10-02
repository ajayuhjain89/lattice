import type { Metadata } from "next";

import { AccessScreen } from "@/components/access/AccessScreen";

export const metadata: Metadata = { title: "Access" };

export default function AccessPage() {
  return <AccessScreen />;
}
