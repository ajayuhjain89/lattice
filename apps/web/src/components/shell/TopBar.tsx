"use client";

import { Menu } from "lucide-react";

import { Icon } from "@/components/primitives/icons";
import { Wordmark } from "@/components/brand/Wordmark";

import styles from "./TopBar.module.css";
import { useWorkspace } from "./workspace-context";

export const TOPBAR_ACTION_ID = "topbar-action";

/** The phone top bar (<768 px): navigation sheet, product, and one page action slot. */
export function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  const { me } = useWorkspace();
  return (
    <div className={styles.bar}>
      <button type="button" className={styles.menu} aria-label="Open navigation" onClick={onOpenMenu}>
        <Icon as={Menu} size={20} />
      </button>
      <div className={styles.brand}>
        <Wordmark />
        <span className={styles.org}>{me.organization.name}</span>
      </div>
      <div className={styles.action} id={TOPBAR_ACTION_ID} />
    </div>
  );
}
