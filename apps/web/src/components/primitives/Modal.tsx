"use client";

import { X } from "lucide-react";
import { Dialog } from "radix-ui";
import type { ReactNode } from "react";

import { Button } from "./Button";
import { Icon } from "./icons";
import styles from "./Modal.module.css";

export interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  /** Actions, right-aligned; the primary action last. */
  footer?: ReactNode;
  size?: "md" | "confirm";
}

/**
 * A centred dialog (design-system B12 "Dialogs"): 560 px (440 for confirmations), ink
 * border and overlay shadow, focus trapped by Radix, Escape closes, focus returns to the
 * control that opened it. Below 768 px it is a full-width sheet anchored to the bottom.
 */
export function Modal({ open, onOpenChange, title, description, children, footer, size = "md" }: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content
          className={`${styles.dialog} ${size === "confirm" ? styles.confirm : ""}`}
          // Without a description the title alone names the dialog (no duplicate reading).
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <header className={styles.header}>
            <Dialog.Title className={styles.title}>{title}</Dialog.Title>
            <Dialog.Close asChild>
              <Button variant="ghost" size="sm" icon={<Icon as={X} />} aria-label="Close" />
            </Dialog.Close>
          </header>
          {description && <Dialog.Description className={styles.description}>{description}</Dialog.Description>}
          <div className={styles.body}>{children}</div>
          {footer && <footer className={styles.footer}>{footer}</footer>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
