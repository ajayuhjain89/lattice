"use client";

import { Popover as P } from "radix-ui";
import type { ReactNode } from "react";

import styles from "./Popover.module.css";

/**
 * A floating panel anchored to its trigger (Radix): Escape and an outside click close it,
 * focus moves in on open and back to the trigger on close. `trigger` must be one
 * focusable element with its own accessible name.
 */
export function Popover({
  trigger,
  children,
  align = "start",
  side = "bottom",
  open,
  onOpenChange,
  label,
  width = "auto",
}: {
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "bottom";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** The panel's accessible name, when its content has no heading of its own. */
  label?: string;
  width?: "auto" | "sm" | "md";
}) {
  return (
    <P.Root open={open} onOpenChange={onOpenChange}>
      <P.Trigger asChild>{trigger}</P.Trigger>
      <P.Portal>
        <P.Content className={styles.content} data-width={width} align={align} side={side} sideOffset={6} collisionPadding={16} aria-label={label}>
          {children}
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}

export const PopoverClose = P.Close;
