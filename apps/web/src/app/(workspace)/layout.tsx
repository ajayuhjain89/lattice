import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/AppShell";

import { Providers } from "../providers";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <Providers>
      <AppShell>{children}</AppShell>
    </Providers>
  );
}
