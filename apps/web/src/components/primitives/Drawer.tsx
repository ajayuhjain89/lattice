"use client";

import { Dialog } from "radix-ui";
import type { ReactNode } from "react";

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
 * the page behind is inert, and focus returns where it came from.
 */
export function Drawer({ open, onOpenChange, side, title, description, children, onOpenAutoFocus, onCloseAutoFocus }: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content
          className={`${styles.panel} ${styles[side]}`}
          onOpenAutoFocus={onOpenAutoFocus}
          onCloseAutoFocus={onCloseAutoFocus}
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
