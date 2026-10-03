"use client";

/**
 * The masthead (≥768 px, design-direction §6.3): product and organization, the
 * destinations by section, Jump, and the account menu, on one 52 px rule.
 *
 * Everything is shown inline whenever it fits. When it doesn't -- a narrow window, 200 %
 * zoom, a long organization name, longer translated labels, a future destination -- the
 * masthead gives up space in a fixed order, measured before paint so nothing flickers:
 *   1. Jump keeps its key hint and drops its word ("Jump" stays its accessible name);
 *   2. sections after the first fold into one menu each, named for the section;
 *   3. the organization name leaves the bar (it is always in the account menu);
 *   4. every destination folds into one "Menu".
 * The first section's destinations stay inline through step 3, so the everyday pages are
 * never one click further away just to save room.
 */
import { ChevronDown, Search } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent } from "react";

import { Wordmark } from "@/components/brand/Wordmark";
import { Icon } from "@/components/primitives/icons";
import { jumpChord } from "@/components/primitives/Jump";
import { Kbd } from "@/components/primitives/Kbd";
import { Menu, MenuLabel, MenuLink, MenuSeparator } from "@/components/primitives/Menu";

import { AccountMenu } from "./AccountMenu";
import styles from "./Masthead.module.css";
import { type Destination, type VisibleSection } from "./nav";

const MAX_LEVEL = 4;

export interface MastheadProps {
  sections: readonly VisibleSection[];
  organization: string;
  onJump: () => void;
  onNavigate: (href: string, event: MouseEvent) => void;
  onSignOut: () => void;
}

export function Masthead({ sections, organization, onJump, onNavigate, onSignOut }: MastheadProps) {
  const pathname = usePathname();
  const row = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState({ level: 0, epoch: 0, content: "" });
  const level = fit.level;
  const chord = jumpChord();

  // Re-fit from the top when the width, the fonts or the content change.
  const refit = () => setFit((f) => ({ ...f, level: 0, epoch: f.epoch + 1 }));
  const content = `${organization}|${sections.map((s) => s.destinations.map((d) => d.label).join(",")).join(";")}`;
  if (fit.content !== content) setFit({ level: 0, epoch: fit.epoch + 1, content });
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        refit();
      }
    });
    observer.observe(el);
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    fonts?.addEventListener?.("loadingdone", refit);
    return () => {
      observer.disconnect();
      fonts?.removeEventListener?.("loadingdone", refit);
    };
  }, []);
  // Step down one level at a time until the row fits (each step renders before paint).
  useLayoutEffect(() => {
    const el = row.current;
    if (el && el.scrollWidth > el.clientWidth + 1 && fit.level < MAX_LEVEL) setFit((f) => ({ ...f, level: f.level + 1 }));
  }, [fit.level, fit.epoch]);

  const [first, ...rest] = sections;
  const link = (d: Destination, className: string) => (
    <Link
      href={d.href}
      className={className}
      aria-current={d.match(pathname) ? "page" : undefined}
      onClick={(e) => onNavigate(d.href, e)}
    >
      {d.label}
    </Link>
  );

  return (
    <header className={styles.masthead} data-level={level} data-shell-nav="">
      <div ref={row} className={styles.row}>
        <Link href="/ask" className={styles.brand} aria-label="Lattice — Ask" onClick={(e) => onNavigate("/ask", e)}>
          <Wordmark />
        </Link>
        {level < 3 && (
          <span className={styles.org} title={organization}>
            {organization}
          </span>
        )}

        <nav aria-label="Primary" className={styles.nav}>
          {level >= 4 ? (
            <SectionMenu label="Menu" sections={sections} pathname={pathname} onNavigate={onNavigate} />
          ) : (
            <>
              {first && (
                <ul className={styles.group} aria-label={first.section.label}>
                  {first.destinations.map((d) => (
                    <li key={d.href}>{link(d, styles.item!)}</li>
                  ))}
                </ul>
              )}
              {rest.map((s) =>
                level >= 2 ? (
                  <div key={s.section.id} className={styles.group}>
                    <SectionMenu label={s.section.short ?? s.section.label} sections={[s]} pathname={pathname} onNavigate={onNavigate} />
                  </div>
                ) : (
                  <ul key={s.section.id} className={styles.group} aria-label={s.section.label}>
                    {s.destinations.map((d) => (
                      <li key={d.href}>{link(d, styles.item!)}</li>
                    ))}
                  </ul>
                ),
              )}
            </>
          )}
        </nav>

        <div className={styles.tools}>
          <button type="button" className={styles.jump} onClick={onJump} aria-keyshortcuts={chord.aria}>
            <Icon as={Search} />
            <span className={level >= 1 ? "visually-hidden" : undefined}>Jump</span>
            <span aria-hidden="true">
              <Kbd>{chord.label}</Kbd>
            </span>
          </button>
          <AccountMenu onSignOut={onSignOut} />
        </div>
      </div>
    </header>
  );
}

/** Destinations folded into a menu. The trigger carries the active mark when it holds the current page. */
function SectionMenu({
  label,
  sections,
  pathname,
  onNavigate,
}: {
  label: string;
  sections: readonly VisibleSection[];
  pathname: string;
  onNavigate: (href: string, event: MouseEvent) => void;
}) {
  const current = sections.flatMap((s) => s.destinations).find((d) => d.match(pathname));
  return (
    <Menu
      align="start"
      label={label}
      trigger={
        <button
          type="button"
          className={styles.item}
          data-current={current ? "" : undefined}
          aria-label={current ? `${label}, current page: ${current.label}` : undefined}
        >
          {label}
          <Icon as={ChevronDown} size={14} />
        </button>
      }
    >
      {sections.map((s, i) => (
        <SectionItems key={s.section.id} section={s} separator={i > 0} labelled={sections.length > 1} pathname={pathname} onNavigate={onNavigate} />
      ))}
    </Menu>
  );
}

function SectionItems({
  section,
  separator,
  labelled,
  pathname,
  onNavigate,
}: {
  section: VisibleSection;
  separator: boolean;
  labelled: boolean;
  pathname: string;
  onNavigate: (href: string, event: MouseEvent) => void;
}) {
  return (
    <>
      {separator && <MenuSeparator />}
      {labelled && <MenuLabel>{section.section.label}</MenuLabel>}
      {section.destinations.map((d) => (
        <MenuLink
          key={d.href}
          href={d.href}
          icon={<Icon as={d.icon} />}
          current={d.match(pathname)}
          onClick={(e) => onNavigate(d.href, e)}
        >
          {d.label}
        </MenuLink>
      ))}
    </>
  );
}
