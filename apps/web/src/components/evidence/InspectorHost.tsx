"use client";

import { useCallback, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";

import { markerId } from "@/components/ask/answer-context";
import { Drawer } from "@/components/primitives/Drawer";
import { useWorkspace } from "@/components/shell/workspace-context";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";
import type { RenderClaim } from "@/lib/answer-model";
import { useStore } from "@/lib/store";
import { CLOSED_INSPECTOR } from "@/lib/workspace-state";

import { EvidenceInspector } from "./EvidenceInspector";
import styles from "./InspectorHost.module.css";

/**
 * Where the inspector lives: docked beside the page at ≥1280 px, a modal drawer at
 * 768–1279 px, a bottom sheet below 768 px (design-system §6).
 */
export function InspectorHost({ className }: { className?: string }) {
  const { state } = useWorkspace();
  const { evidenceId, context } = useStore(state.inspector, (s) => s);
  const docked = useMinWidth(BREAKPOINTS.docked);
  const phone = !useMinWidth(BREAKPOINTS.mobile);
  const pathname = usePathname();

  // In drawer mode focus can only move once Radix has released its focus trap, which it
  // signals with onCloseAutoFocus; docked, the panel traps nothing and focus moves now.
  const pendingFocus = useRef<HTMLElement | null>(null);
  const close = useCallback(() => {
    const { returnFocus } = state.inspector.get();
    state.inspector.set(CLOSED_INSPECTOR);
    if (docked) {
      if (returnFocus?.isConnected) returnFocus.focus();
    } else {
      pendingFocus.current = returnFocus;
    }
  }, [state.inspector, docked]);

  const navigate = useCallback(
    (id: string) => state.inspector.set((s) => ({ ...s, evidenceId: id, claim: null })),
    [state.inspector],
  );

  // "Show" beside a citing claim: back to that claim's marker in the answer. Docked, the
  // panel stays open; as a drawer or sheet it closes first, and focus lands on the marker.
  const showClaim = useCallback(
    (claim: RenderClaim) => {
      const { evidenceId: id, context: ctx } = state.inspector.get();
      const n = id ? ctx?.numberOf.get(id) : undefined;
      const marker = n !== undefined ? document.getElementById(markerId(claim.key, n)) : null;
      if (!marker) return;
      if (docked) {
        marker.scrollIntoView({ block: "center" });
        marker.focus({ preventScroll: true });
        state.inspector.set((s) => ({ ...s, claim: claim.key, returnFocus: marker }));
      } else {
        pendingFocus.current = marker;
        state.inspector.set(CLOSED_INSPECTOR);
      }
    },
    [state.inspector, docked],
  );

  // A new page closes the inspector: its context belongs to the previous page.
  useEffect(() => {
    state.inspector.set(CLOSED_INSPECTOR);
  }, [pathname, state.inspector]);

  if (!evidenceId) return null;

  const inspector = (
    <EvidenceInspector evidenceId={evidenceId} context={context} onClose={close} onNavigate={navigate} onShowClaim={showClaim} focusOnOpen />
  );

  if (docked) {
    return (
      <aside className={`${styles.docked} ${className ?? ""}`} aria-label="Evidence inspector">
        {inspector}
      </aside>
    );
  }
  return (
    <Drawer
      open
      side={phone ? "bottom" : "right"}
      title="Evidence inspector"
      onOpenChange={(open) => !open && close()}
      onOpenAutoFocus={(e) => e.preventDefault()}
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        const target = pendingFocus.current;
        pendingFocus.current = null;
        if (target?.isConnected) target.focus();
      }}
    >
      {inspector}
    </Drawer>
  );
}
