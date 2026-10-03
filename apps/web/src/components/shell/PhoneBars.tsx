"use client";

/**
 * Phone navigation (<768 px, design-direction §6.3): a 52 px top bar for identity and
 * context, and a tab bar at the bottom for the most-used destinations. Everything else --
 * administration, any destination beyond the tab limit, the account and sign out -- is
 * in the More sheet, so nothing scrolls sideways and every label has room.
 */
import { LogOut, MoreHorizontal, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState, type MouseEvent } from "react";

import { Mark } from "@/components/brand/Mark";
import { Button } from "@/components/primitives/Button";
import { Drawer, DrawerClose } from "@/components/primitives/Drawer";
import { Icon } from "@/components/primitives/icons";
import { jumpChord } from "@/components/primitives/Jump";

import { AccountSummary } from "./AccountMenu";
import { currentDestination, phoneSplit, type VisibleSection } from "./nav";
import styles from "./PhoneBars.module.css";

/** Where a page may put one action in the phone top bar (e.g. Ask's "New question"). */
export const TOPBAR_ACTION_ID = "topbar-action";

export function PhoneTopBar({ sections, organization, onJump, onNavigate }: { sections: readonly VisibleSection[]; organization: string; onJump: () => void; onNavigate: (href: string, event: MouseEvent) => void }) {
  const pathname = usePathname();
  const here = currentDestination(
    sections.flatMap((s) => s.destinations),
    pathname,
  );
  return (
    <header className={styles.top} data-shell-nav="">
      <Link href="/ask" className={styles.home} aria-label="Lattice — Ask" onClick={(e) => onNavigate("/ask", e)}>
        <Mark size={20} />
      </Link>
      <div className={styles.context}>
        {here && <span className={styles.page}>{here.label}</span>}
        <span className={styles.org} title={organization}>
          {organization}
        </span>
      </div>
      <div className={styles.action} id={TOPBAR_ACTION_ID} />
      <button type="button" className={styles.icon} aria-label="Jump" aria-keyshortcuts={jumpChord().aria} onClick={onJump}>
        <Icon as={Search} size={20} />
      </button>
    </header>
  );
}

export function PhoneBottomBar({
  sections,
  onNavigate,
  onSignOut,
  signingOut,
}: {
  sections: readonly VisibleSection[];
  onNavigate: (href: string, event: MouseEvent) => void;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { tabs, more } = phoneSplit(sections);
  const inMore = more.flatMap((s) => s.destinations).find((d) => d.match(pathname));
  // A link chosen in the sheet decides where focus goes next (the page), not the sheet.
  const navigated = useRef(false);

  return (
    <>
      <nav aria-label="Primary" className={styles.bottom} data-shell-nav="">
        <ul className={styles.tabs}>
          {tabs.map((d) => (
            <li key={d.href}>
              <Link href={d.href} className={styles.tab} aria-current={d.match(pathname) ? "page" : undefined} onClick={(e) => onNavigate(d.href, e)}>
                <Icon as={d.icon} size={20} />
                <span className={styles.tabLabel}>{d.label}</span>
              </Link>
            </li>
          ))}
          <li>
            <button
              type="button"
              className={styles.tab}
              data-current={inMore ? "" : undefined}
              aria-label={inMore ? `More, current page: ${inMore.label}` : undefined}
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => {
                navigated.current = false;
                setOpen(true);
              }}
            >
              <Icon as={MoreHorizontal} size={20} />
              <span className={styles.tabLabel}>More</span>
            </button>
          </li>
        </ul>
      </nav>

      <Drawer
        open={open}
        onOpenChange={setOpen}
        side="bottom"
        fit
        title="More"
        onCloseAutoFocus={(e) => {
          if (navigated.current) e.preventDefault();
        }}
      >
        <div className={styles.sheet}>
          <div className={styles.sheetHead}>
            <h2 className={styles.sheetTitle}>More</h2>
            <DrawerClose asChild>
              <Button variant="quiet" size="lg" icon={<Icon as={X} size={20} />} aria-label="Close" />
            </DrawerClose>
          </div>
          {more.map(({ section, destinations }) => (
            <section key={section.id} className={styles.sheetSection} aria-labelledby={`more-${section.id}`}>
              <h3 id={`more-${section.id}`} className={styles.sheetLabel}>
                {section.label}
              </h3>
              <ul className={styles.sheetList}>
                {destinations.map((d) => (
                  <li key={d.href}>
                    <Link
                      href={d.href}
                      className={styles.sheetLink}
                      aria-current={d.match(pathname) ? "page" : undefined}
                      onClick={(e) => {
                        navigated.current = true;
                        setOpen(false);
                        onNavigate(d.href, e);
                      }}
                    >
                      <Icon as={d.icon} size={20} />
                      <span>{d.label}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <section className={styles.sheetSection} aria-labelledby="more-account">
            <h3 id="more-account" className={styles.sheetLabel}>
              Signed in
            </h3>
            <div className={styles.account}>
              <AccountSummary />
            </div>
            <Button size="lg" icon={<Icon as={LogOut} />} onClick={onSignOut} loading={signingOut} className={styles.signOut}>
              Sign out
            </Button>
          </section>
        </div>
      </Drawer>
    </>
  );
}
