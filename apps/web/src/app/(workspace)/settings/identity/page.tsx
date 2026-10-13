import type { Metadata } from "next";
import { Suspense } from "react";

import { IdentityScreen } from "@/components/settings/IdentityScreen";

export const metadata: Metadata = { title: "Identity" };

export default function IdentityPage() {
  // The page reads ?test= (a finished test sign-in): search parameters need a boundary.
  return (
    <Suspense>
      <IdentityScreen />
    </Suspense>
  );
}
