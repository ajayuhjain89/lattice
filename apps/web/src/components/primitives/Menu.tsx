"use client";

import Link from "next/link";
import { DropdownMenu as M } from "radix-ui";
import type { MouseEvent, ReactNode } from "react";

import styles from "./Menu.module.css";

/**
 * A menu of actions (Radix DropdownMenu): arrow keys move, typing jumps to an item,
 * Enter or Space chooses, Escape closes and focus returns to the trigger. For choosing a
 * value use a Select or a ChoiceGroup instead -- a menu performs actions.
 */
export function Menu({ trigger, children, align = "end", label }: { trigger: ReactNode; children: ReactNode; align?: "start" | "end"; label?: string }) {
  return (
    // Non-modal: a menu isn't a dialog, so the page isn't hidden from assistive tech
    // while it's open (focus still moves in, and Escape returns it).
    <M.Root modal={false}>
      <M.Trigger asChild>{trigger}</M.Trigger>
      <M.Portal>
        <M.Content className={styles.content} align={align} sideOffset={6} collisionPadding={16} aria-label={label}>
          {children}
        </M.Content>
      </M.Portal>
    </M.Root>
  );
}

interface ItemProps {
  children: ReactNode;
  icon?: ReactNode;
  /** Secondary text on the right (a shortcut, a count). */
  hint?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
}

function Inner({ icon, children, hint }: Pick<ItemProps, "icon" | "children" | "hint">) {
  return (
    <>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      <span className={styles.text}>{children}</span>
      {hint && <span className={styles.hint}>{hint}</span>}
    </>
  );
}

export function MenuItem({ onSelect, danger = false, disabled, ...rest }: ItemProps & { onSelect: () => void }) {
  return (
    <M.Item className={[styles.item, danger && styles.danger].filter(Boolean).join(" ")} onSelect={onSelect} disabled={disabled}>
      <Inner {...rest} />
    </M.Item>
  );
}

export function MenuLink({
  href,
  current = false,
  onClick,
  ...rest
}: ItemProps & { href: string; /** The page this link opens is the one on screen. */ current?: boolean; onClick?: (event: MouseEvent<HTMLAnchorElement>) => void }) {
  return (
    <M.Item asChild className={styles.item} disabled={rest.disabled}>
      <Link href={href} aria-current={current ? "page" : undefined} onClick={onClick}>
        <Inner {...rest} />
      </Link>
    </M.Item>
  );
}

/** A non-interactive heading block inside the menu (e.g. who is signed in). */
export function MenuLabel({ children }: { children: ReactNode }) {
  return <M.Label className={styles.label}>{children}</M.Label>;
}

export function MenuSeparator() {
  return <M.Separator className={styles.separator} />;
}
