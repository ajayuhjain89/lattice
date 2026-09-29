"use client";

import { Tooltip as T } from "radix-ui";
import type { ReactNode } from "react";

import styles from "./Tooltip.module.css";

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <T.Provider delayDuration={300} skipDelayDuration={150}>
    {children}
  </T.Provider>
);

/**
 * A supplementary label. Never the only way to get information needed for a task: the
 * trigger always carries its own accessible name.
 */
export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content className={styles.content} side={side} sideOffset={6} collisionPadding={8}>
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
