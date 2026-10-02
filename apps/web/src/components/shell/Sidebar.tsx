"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { Tooltip } from "@/components/primitives/Tooltip";
import { Wordmark } from "@/components/brand/Wordmark";
import { useStore } from "@/lib/store";

import { NAV } from "./nav";
import styles from "./Sidebar.module.css";
import { shortStatus } from "./status-labels";
import { useWorkspace } from "./workspace-context";

/** The full sidebar: docked at ≥1280 px, and the content of the navigation sheet below. */
export function Sidebar({ onNavigate, onSignOut, signingOut }: { onNavigate?: () => void; onSignOut: () => void; signingOut: boolean }) {
  const { me, state, isAdmin } = useWorkspace();
  const pathname = usePathname();
  const history = useStore(state.history, (h) => h);
  const main = NAV.filter((n) => !n.admin);
  const admin = NAV.filter((n) => n.admin && isAdmin);

  return (
    <div className={styles.sidebar}>
      <div className={styles.header}>
        <Link href="/ask" className={styles.homeLink} onClick={onNavigate} aria-label="Lattice — Ask">
          <Wordmark />
        </Link>
        <span className={styles.divider} aria-hidden="true" />
        <span className={styles.org} title={me.organization.name}>
          {me.organization.name}
        </span>
      </div>

      <nav className={styles.scroll} aria-label="Primary">
        <ul className={styles.group}>
          {main.map((n) => (
            <li key={n.href}>
              <Link href={n.href} className={styles.item} aria-current={n.match(pathname) ? "page" : undefined} onClick={onNavigate}>
                <span className={styles.itemIcon}>
                  <Icon as={n.icon} />
                </span>
                <span className={styles.itemLabel}>{n.label}</span>
              </Link>
            </li>
          ))}
        </ul>

        {history.length > 0 && (
          <div className={styles.group}>
            <h2 className={styles.groupLabel} id="nav-session">
              This session
            </h2>
            <ul className={styles.history} aria-labelledby="nav-session">
              {history.map((h) => {
                const href = `/traces/${h.traceId}`;
                return (
                  <li key={h.traceId}>
                    <Link href={href} className={styles.historyItem} aria-current={pathname === href ? "page" : undefined} onClick={onNavigate}>
                      <span className={styles.historyQuestion} title={h.question}>
                        {h.question}
                      </span>
                      <span className={styles.historyStatus}>{shortStatus(h.status)} · trace</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <p className={styles.historyNote}>Kept in this tab only. Traces are re-checked against your access each time.</p>
          </div>
        )}

        {admin.length > 0 && (
          <div className={styles.group}>
            <h2 className={styles.groupLabel} id="nav-admin">
              Admin
            </h2>
            <ul aria-labelledby="nav-admin">
              {admin.map((n) => (
                <li key={n.href}>
                  <Link href={n.href} className={styles.item} aria-current={n.match(pathname) ? "page" : undefined} onClick={onNavigate}>
                    <span className={styles.itemIcon}>
                      <Icon as={n.icon} />
                    </span>
                    <span className={styles.itemLabel}>{n.label}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </nav>

      <div className={styles.footer}>
        <div className={styles.user}>
          <span className={styles.userName}>{me.user.display_name}</span>
          <span className={styles.userRole}>{me.membership.role_label ?? me.user.email}</span>
        </div>
        <Tooltip content="Sign out">
          <Button variant="quiet" size="sm" icon={<Icon as={LogOut} />} aria-label="Sign out" onClick={onSignOut} loading={signingOut} />
        </Tooltip>
      </div>
    </div>
  );
}
