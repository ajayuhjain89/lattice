"use client";

import { LogOut, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { Tooltip } from "@/components/primitives/Tooltip";

import { NAV } from "./nav";
import styles from "./Rail.module.css";
import { useWorkspace } from "./workspace-context";

/** The 56 px rail (768–1279 px): icons with names; the full sidebar opens as a sheet. */
export function Rail({ onOpenMenu, onSignOut, signingOut }: { onOpenMenu: () => void; onSignOut: () => void; signingOut: boolean }) {
  const { isAdmin } = useWorkspace();
  const pathname = usePathname();
  return (
    <div className={styles.rail}>
      <div className={styles.header}>
        <Tooltip content="Open navigation" side="right">
          <Button variant="quiet" icon={<Icon as={PanelLeftOpen} />} aria-label="Open navigation" onClick={onOpenMenu} />
        </Tooltip>
      </div>
      <nav aria-label="Primary" className={styles.nav}>
        {NAV.filter((n) => !n.admin || isAdmin).map((n) => (
          <Tooltip key={n.href} content={n.label} side="right">
            <Link href={n.href} className={styles.item} aria-label={n.label} aria-current={n.match(pathname) ? "page" : undefined}>
              <Icon as={n.icon} />
            </Link>
          </Tooltip>
        ))}
      </nav>
      <div className={styles.footer}>
        <Tooltip content="Sign out" side="right">
          <Button variant="quiet" icon={<Icon as={LogOut} />} aria-label="Sign out" onClick={onSignOut} loading={signingOut} />
        </Tooltip>
      </div>
    </div>
  );
}
