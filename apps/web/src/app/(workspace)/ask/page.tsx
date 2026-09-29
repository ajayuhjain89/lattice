import type { Metadata } from "next";

import { AskScreen } from "@/components/ask/AskScreen";

export const metadata: Metadata = { title: "Ask" };

export default function AskPage() {
  return <AskScreen />;
}
