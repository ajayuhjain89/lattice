"use client";

/**
 * Jump (⌘K / Ctrl K): go anywhere, or ask, from the keyboard (design-direction §6.3).
 *
 * It searches only the items it is given -- destinations, the viewer's own recent
 * questions, and for administrators the members and sources their endpoints already
 * return. It never queries documents or titles (security-model §10, "Autocomplete: none
 * in the slice"), so it can't become a way to enumerate anything.
 *
 * ARIA combobox pattern: the input owns a listbox through aria-activedescendant; ↑ / ↓
 * move, Enter chooses, Escape closes and focus returns to where it was.
 */
import { CornerDownLeft, Search, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import type { Glyph } from "./glyphs";
import { Icon } from "./icons";
import styles from "./Jump.module.css";
import { Kbd } from "./Kbd";

export interface JumpItem {
  id: string;
  group: string;
  label: string;
  /** Quiet text after the label (a role, a status, a date). */
  detail?: string;
  /** Extra words to match on, never shown. */
  keywords?: string;
  icon?: LucideIcon | Glyph;
  href?: string;
  onSelect?: () => void;
}

export interface JumpGroupStatus {
  group: string;
  /** e.g. "Loading recent questions…" or "Couldn't load members." */
  message: string;
}

export interface JumpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: readonly JumpItem[];
  /** Group order; groups with no matching items are left out. */
  groups: readonly string[];
  statuses?: readonly JumpGroupStatus[];
  /** With text typed, the first option asks it ("Ask “…”"). */
  onAsk?: (question: string) => void;
  /** The typed text, for a caller whose items come from a search (cleared on close). */
  onQueryChange?: (query: string) => void;
  placeholder?: string;
}

function matches(item: JumpItem, terms: string[]): number {
  if (!terms.length) return 1;
  const label = item.label.toLowerCase();
  const hay = `${label} ${item.detail ?? ""} ${item.keywords ?? ""}`.toLowerCase();
  if (!terms.every((t) => hay.includes(t))) return 0;
  // Rank: label starts with the query, then a word of the label starts with it, then anywhere.
  const first = terms[0]!;
  if (label.startsWith(first)) return 3;
  if (label.split(/\s+/).some((w) => w.startsWith(first))) return 2;
  return 1;
}

export function Jump({
  open,
  onOpenChange,
  items,
  groups,
  statuses = [],
  onAsk,
  onQueryChange,
  placeholder = "Jump to a page, a recent question, or ask…",
}: JumpProps) {
  const router = useRouter();
  const [query, setQueryState] = useState("");
  const setQuery = (next: string) => {
    setQueryState(next);
    onQueryChange?.(next);
  };
  const [active, setActive] = useState(0);
  const listId = useId();
  const list = useRef<HTMLDivElement>(null);
  // Opened by a shortcut or an ordinary button: remember where focus was, and go back.
  const opener = useRef<HTMLElement | null>(null);
  // A chosen item decides where focus goes (a page, the composer); only a dismissal restores.
  const chose = useRef(false);

  const canAsk = Boolean(onAsk);
  const sections = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const out: { group: string; items: JumpItem[] }[] = [];
    if (canAsk && query.trim())
      out.push({
        group: "Ask",
        items: [
          { id: "__ask", group: "Ask", label: `Ask “${query.trim()}”`, icon: CornerDownLeft },
        ],
      });
    for (const group of groups) {
      const found = items
        .filter((i) => i.group === group)
        .map((i) => ({ i, score: matches(i, terms) }))
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .map((x) => x.i);
      if (found.length) out.push({ group, items: found });
    }
    return out;
  }, [items, groups, query, canAsk]);
  const flat = sections.flatMap((s) => s.items);
  const current = Math.min(active, Math.max(flat.length - 1, 0));
  const optionId = (index: number) => `${listId}-option-${index}`;

  useEffect(() => {
    list.current
      ?.querySelector(`#${CSS.escape(`${listId}-option-${current}`)}`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [current, listId]);

  const choose = (item: JumpItem | undefined) => {
    if (!item) return;
    chose.current = true;
    onOpenChange(false);
    setQuery("");
    setActive(0);
    if (item.id === "__ask") onAsk?.(query.trim());
    else if (item.onSelect) item.onSelect();
    else if (item.href) router.push(item.href);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!flat.length) return;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + delta + flat.length) % flat.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(flat[current]);
    }
  };

  let index = -1;
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setQuery("");
          setActive(0);
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content
          className={styles.panel}
          aria-describedby={undefined}
          data-jump=""
          onOpenAutoFocus={() => {
            opener.current =
              document.activeElement instanceof HTMLElement ? document.activeElement : null;
            chose.current = false;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (!chose.current) opener.current?.focus();
          }}
        >
          <Dialog.Title className="visually-hidden">Jump</Dialog.Title>
          <div className={styles.inputRow}>
            <span className={styles.searchIcon} aria-hidden="true">
              <Icon as={Search} />
            </span>
            <input
              className={styles.input}
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={flat.length ? optionId(current) : undefined}
              aria-label="Jump to"
              placeholder={placeholder}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={onKeyDown}
              autoComplete="off"
              spellCheck={false}
            />
            <Kbd>Esc</Kbd>
          </div>
          <div ref={list} id={listId} role="listbox" aria-label="Results" className={styles.list}>
            {sections.map((section, si) => (
              <div key={section.group} role="presentation" className={styles.section}>
                <span className={styles.groupLabel} id={`${listId}-group-${si}`} aria-hidden="true">
                  {section.group}
                </span>
                <div
                  role="group"
                  aria-labelledby={`${listId}-group-${si}`}
                  className={styles.groupList}
                >
                  {section.items.map((item) => {
                    index += 1;
                    const i = index;
                    /* Keyboard selection happens in the combobox input (aria-activedescendant),
                       so options take a pointer click only and never take focus. */
                    /* eslint-disable jsx-a11y/click-events-have-key-events, no-restricted-syntax */
                    return (
                      <div
                        onClick={() => choose(item)}
                        key={item.id}
                        tabIndex={-1}
                        onMouseDown={(e) => e.preventDefault()}
                        id={optionId(i)}
                        role="option"
                        aria-selected={i === current}
                        className={styles.option}
                        onMouseMove={() => i !== current && setActive(i)}
                      >
                        {item.icon && (
                          <span className={styles.optionIcon} aria-hidden="true">
                            <Icon as={item.icon} />
                          </span>
                        )}
                        <span className={styles.optionLabel} title={item.label}>
                          {item.label}
                        </span>
                        {item.detail && (
                          <>
                            <span className="visually-hidden">,</span>{" "}
                            <span className={styles.optionDetail}>{item.detail}</span>
                          </>
                        )}
                      </div>
                    );
                    /* eslint-enable jsx-a11y/click-events-have-key-events, no-restricted-syntax */
                  })}
                </div>
              </div>
            ))}
          </div>
          <Footer statuses={statuses} count={flat.length} query={query} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Footer({
  statuses,
  count,
  query,
}: {
  statuses: readonly JumpGroupStatus[];
  count: number;
  query: string;
}) {
  const summary: ReactNode =
    count === 0
      ? query.trim()
        ? "Nothing matches."
        : "Nothing to jump to yet."
      : `${count} ${count === 1 ? "result" : "results"}`;
  return (
    <div className={styles.footer}>
      <p className={styles.status} role="status" aria-live="polite">
        {summary}
        {statuses.map((s) => (
          <span key={s.group} className={styles.groupStatus}>
            {" "}
            · {s.message}
          </span>
        ))}
      </p>
      <p className={styles.keys} aria-hidden="true">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd> move · <Kbd>↵</Kbd> open
      </p>
    </div>
  );
}

/**
 * The platform's Jump chord: ⌘K on Apple platforms, Ctrl K elsewhere. Only the platform's
 * own modifier is taken, so Ctrl K on a Mac keeps its text-editing meaning (delete to the
 * end of the line) in the composer and every other field.
 */
export function jumpChord(): { apple: boolean; label: string; aria: string } {
  const nav = typeof navigator === "undefined" ? null : navigator;
  const platform =
    (nav as (Navigator & { userAgentData?: { platform?: string } }) | null)?.userAgentData?.platform ||
    nav?.platform ||
    "";
  const apple = /mac|iphone|ipad|ipod/i.test(platform);
  return apple ? { apple, label: "⌘K", aria: "Meta+K" } : { apple, label: "Ctrl K", aria: "Control+K" };
}

/**
 * ⌘K / Ctrl K toggles Jump from anywhere in the workspace -- including inside a text
 * field -- except while another modal dialog owns the page (a confirmation must be
 * finished or dismissed first), during IME composition, and on key repeat.
 */
export function useJumpShortcut(onToggle: () => void) {
  useEffect(() => {
    const { apple } = jumpChord();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k" || event.altKey || event.shiftKey) return;
      if (apple ? !event.metaKey || event.ctrlKey : !event.ctrlKey || event.metaKey) return;
      if (event.isComposing || event.repeat) return;
      // Modal and Drawer mark their content with data-modal; popovers and menus don't block.
      if (document.querySelector('[data-modal][data-state="open"]')) return;
      event.preventDefault();
      onToggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onToggle]);
}
