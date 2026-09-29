"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useMe, useSignOut } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { InspectorHost } from "@/components/evidence/InspectorHost";
import { Drawer } from "@/components/primitives/Drawer";
import { ErrorState } from "@/components/states/ErrorState";
import { createWorkspaceState, saveDraft, type WorkspaceState } from "@/lib/workspace-state";

import styles from "./AppShell.module.css";
import { Rail } from "./Rail";
import { ShellSkeleton } from "./ShellSkeleton";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { WorkspaceProvider } from "./workspace-context";

/**
 * The signed-in workspace. Identity and capabilities come from /v1/me only; the admin
 * navigation is rendered from its answer and every admin endpoint still enforces it.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const client = useQueryClient();
  const [state, setState] = useState<WorkspaceState>(createWorkspaceState);

  // Any 401 anywhere means the session is gone: keep the draft, go to sign-in.
  useEffect(() => {
    const toSignIn = (error: unknown) => {
      if (!isApiError(error) || !error.isUnauthenticated) return;
      saveDraft(state.draft.get());
      client.clear();
      router.replace(`/sign-in?next=${encodeURIComponent(pathname)}`);
    };
    const offQueries = client.getQueryCache().subscribe((e) => {
      if (e.type === "updated" && e.action.type === "error") toSignIn(e.action.error);
    });
    const offMutations = client.getMutationCache().subscribe((e) => {
      if (e.type === "updated" && e.action.type === "error") toSignIn(e.action.error);
    });
    return () => {
      offQueries();
      offMutations();
    };
  }, [client, router, pathname, state.draft]);

  useEffect(() => {
    if (me.isError && isApiError(me.error) && me.error.isUnauthenticated) {
      router.replace(`/sign-in?next=${encodeURIComponent(pathname)}`);
    }
  }, [me.isError, me.error, router, pathname]);

  if (me.isError && !(isApiError(me.error) && me.error.isUnauthenticated)) {
    return (
      <main id="main" className={styles.fatal}>
        <ErrorState error={me.error} title="Lattice couldn't start your session" headingLevel="h1" onRetry={() => me.refetch()} retrying={me.isFetching} />
      </main>
    );
  }
  if (!me.data) return <ShellSkeleton />;

  return (
    <WorkspaceProvider value={{ me: me.data, state, isAdmin: me.data.capabilities.includes("admin") }}>
      <Shell onSignedOut={() => setState(createWorkspaceState())}>{children}</Shell>
    </WorkspaceProvider>
  );
}

function Shell({ children, onSignedOut }: { children: ReactNode; onSignedOut: () => void }) {
  const router = useRouter();
  const signOut = useSignOut();
  const [menuOpen, setMenuOpen] = useState(false);

  const onSignOut = () =>
    signOut.mutate(undefined, {
      onSettled: () => {
        onSignedOut();
        router.replace("/sign-in");
      },
    });

  return (
    <div className={styles.shell}>
      <a href="#main" className={styles.skip}>
        Skip to main content
      </a>
      <div className={styles.sidebar}>
        <Sidebar onSignOut={onSignOut} signingOut={signOut.isPending} />
      </div>
      <div className={styles.rail}>
        <Rail onOpenMenu={() => setMenuOpen(true)} onSignOut={onSignOut} signingOut={signOut.isPending} />
      </div>
      <div className={styles.topbar}>
        <TopBar onOpenMenu={() => setMenuOpen(true)} />
      </div>
      <Drawer open={menuOpen} onOpenChange={setMenuOpen} side="left" title="Navigation">
        <Sidebar onNavigate={() => setMenuOpen(false)} onSignOut={onSignOut} signingOut={signOut.isPending} />
      </Drawer>
      <div className={styles.page}>{children}</div>
      <InspectorHost className={styles.inspector} />
    </div>
  );
}
