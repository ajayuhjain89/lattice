"use client";

import { Dialog } from "radix-ui";
import { useRef, type ReactNode } from "react";

import styles from "./Drawer.module.css";

export interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side: "left" | "right" | "bottom";
  /** Accessible name of the dialog; rendered visually by the content itself. */
  title: string;
  description?: string;
  children: ReactNode;
  onOpenAutoFocus?: (event: Event) => void;
  onCloseAutoFocus?: (event: Event) => void;
}

/**
 * A modal side panel or bottom sheet (Radix Dialog): focus is trapped, Escape closes,
 * the page behind is inert, and focus returns where it came from. Radix returns focus
 * only to its own trigger, so the opener is remembered; a caller with its own focus
 * handlers (the inspector) runs first and may call preventDefault to take over.
 */
export function Drawer({ open, onOpenChange, side, title, description, children, onOpenAutoFocus, onCloseAutoFocus }: DrawerProps) {
  const opener = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content
          className={`${styles.panel} ${styles[side]}`}
          onOpenAutoFocus={(event) => {
            opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            onOpenAutoFocus?.(event);
          }}
          onCloseAutoFocus={(event) => {
            onCloseAutoFocus?.(event);
            if (event.defaultPrevented) return;
            event.preventDefault();
            opener.current?.focus();
          }}
        >
          <Dialog.Title className="visually-hidden">{title}</Dialog.Title>
          <Dialog.Description className="visually-hidden">{description ?? title}</Dialog.Description>
          {side === "bottom" && <div className={styles.handle} aria-hidden="true" />}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const DrawerClose = Dialog.Close;
