"use client";

/**
 * Filters that read as a sentence (design-direction §6.5, §6.11):
 *
 *   Search  all my sources · Acme Corp ✕ · any date · as Internal
 *
 * Each token is a button showing its current value; it opens a popover with the choices.
 * A token whose value narrows the default gets its own clear button (a separate target).
 * Tokens only narrow -- the API decides what the person can read.
 */
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "./Button";
import styles from "./FilterLine.module.css";
import { Icon } from "./icons";
import { Popover, PopoverClose } from "./Popover";

export function FilterLine({ lead, label, children }: { lead?: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className={styles.line} role="group" aria-label={label}>
      {lead && <span className={styles.lead}>{lead}</span>}
      {children}
    </div>
  );
}

export interface FilterTokenProps {
  /** What the token filters ("Sources", "Dates"); part of the accessible name. */
  name: string;
  /** The current value as words ("all my sources", "Acme Corp"). */
  value: string;
  /** The value narrows the default (it can be cleared). */
  set?: boolean;
  invalid?: boolean;
  onClear?: () => void;
  /** Words before the value inside the sentence ("as"). */
  prefix?: string;
  children: ReactNode;
}

export function FilterToken({ name, value, set = false, invalid = false, onClear, prefix, children }: FilterTokenProps) {
  return (
    <span className={[styles.token, set && styles.set, invalid && styles.invalid].filter(Boolean).join(" ")}>
      {prefix && <span className={styles.prefix}>{prefix}</span>}
      <Popover
        width="md"
        trigger={
          <button type="button" className={styles.trigger} aria-label={`${name}: ${value}${invalid ? ", needs a fix" : ""}. Change`}>
            {value}
          </button>
        }
      >
        <div className={styles.panel}>
          {children}
          <div className={styles.actions}>
            {onClear && (
              <Button size="sm" variant="quiet" onClick={onClear} disabled={!set}>
                Clear
              </Button>
            )}
            <PopoverClose asChild>
              <Button size="sm">Done</Button>
            </PopoverClose>
          </div>
        </div>
      </Popover>
      {set && onClear && (
        <button type="button" className={styles.clear} onClick={onClear} aria-label={`Remove ${name.toLowerCase()} filter`}>
          <Icon as={X} size={12} />
        </button>
      )}
    </span>
  );
}
