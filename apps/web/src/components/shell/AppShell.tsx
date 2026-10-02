"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";

import { useMe, useSignOut } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { InspectorHost } from "@/components/evidence/InspectorHost";
import { useJumpShortcut } from "@/components/primitives/Jump";
import { ErrorState } from "@/components/states/ErrorState";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";
import { createWorkspaceState, saveDraft, type WorkspaceState } from "@/lib/workspace-state";

import styles from "./AppShell.module.css";
import { Masthead } from "./Masthead";
import { visibleNav } from "./nav";
import { PhoneBottomBar, PhoneTopBar } from "./PhoneBars";
import { ShellJump } from "./ShellJump";
import { ShellSkeleton } from "./ShellSkeleton";
import { WorkspaceProvider, useWorkspace } from "./workspace-context";

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

/**
 * After a navigation the page owns focus: if focus was left on the shell's own navigation
 * (or nowhere, because what held it went away), it moves to the page's main region so the
 * next Tab continues in the new page. A page that has already put focus somewhere -- the
 * composer, after "Ask “…”" in Jump -- keeps it.
 */
function focusPageIfLost() {
  requestAnimationFrame(() => {
    const active = document.activeElement;
    const main = document.getElementById("main");
    if (!main) return;
    if (!active || active === document.body || active.closest("[data-shell-nav]")) main.focus({ preventScroll: true });
  });
}

function Shell({ children, onSignedOut }: { children: ReactNode; onSignedOut: () => void }) {
  const { me, state } = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const signOut = useSignOut();
  const phone = !useMinWidth(BREAKPOINTS.mobile);
  const sections = visibleNav(me);
  const [jumpOpen, setJumpOpen] = useState(false);

  useJumpShortcut(useCallback(() => setJumpOpen((open) => !open), []));

  const previous = useRef(pathname);
  useEffect(() => {
    if (previous.current === pathname) return;
    previous.current = pathname;
    focusPageIfLost();
  }, [pathname]);

  // A link to the page already on screen doesn't navigate; focus still moves to the page.
  const onNavigate = useCallback(
    (href: string, event: MouseEvent) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
      if (href === pathname) focusPageIfLost();
    },
    [pathname],
  );
  const go = useCallback(
    (href: string) => {
      if (href === pathname) focusPageIfLost();
      else router.push(href);
    },
    [pathname, router],
  );
  const ask = useCallback(
    (question: string) => {
      state.prefill.set(question);
      if (pathname !== "/ask") router.push("/ask");
    },
    [pathname, router, state.prefill],
  );

  const onSignOut = () =>
    signOut.mutate(undefined, {
      onSettled: () => {
        onSignedOut();
        router.replace("/sign-in");
      },
    });

  // The page reserves exactly the phone tab bar's height (it grows when labels wrap).
  const bottom = useRef<HTMLDivElement>(null);
  const [bottomInset, setBottomInset] = useState<number | null>(null);
  useEffect(() => {
    const bar = bottom.current?.firstElementChild;
    if (!phone || !bar) return;
    const observer = new ResizeObserver(() => setBottomInset(bar.getBoundingClientRect().height));
    observer.observe(bar);
    return () => observer.disconnect();
  }, [phone]);

  return (
    <div
      className={styles.shell}
      data-layout={phone ? "phone" : "masthead"}
      style={bottomInset !== null && phone ? ({ "--shell-bottom-inset": `${bottomInset}px` } as CSSProperties) : undefined}
    >
      <a href="#main" className={styles.skip}>
        Skip to main content
      </a>
      {phone ? (
        <PhoneTopBar sections={sections} organization={me.organization.name} onJump={() => setJumpOpen(true)} onNavigate={onNavigate} />
      ) : (
        <Masthead sections={sections} organization={me.organization.name} onJump={() => setJumpOpen(true)} onNavigate={onNavigate} onSignOut={onSignOut} />
      )}
      <div className={styles.canvas}>
        <main id="main" tabIndex={-1} className={styles.main}>
          {children}
        </main>
        <InspectorHost className={styles.inspector} />
      </div>
      {phone && (
        <div ref={bottom}>
          <PhoneBottomBar sections={sections} onNavigate={onNavigate} onSignOut={onSignOut} signingOut={signOut.isPending} />
        </div>
      )}
      <ShellJump open={jumpOpen} onOpenChange={setJumpOpen} onGo={go} onAsk={ask} />
    </div>
  );
}
