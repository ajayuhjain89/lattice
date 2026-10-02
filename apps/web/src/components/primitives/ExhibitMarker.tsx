"use client";

import { X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";

import styles from "./ExhibitMarker.module.css";

/**
 * The exhibit marker (design-direction §5.1): one squared numeral that ties a claim to
 * its source, wherever it appears -- after a claim, in the margin, on an exhibit row, in
 * the inspector header, on a trace stop.
 *
 *   rest      sheet, control-boundary edge
 *   linked    a related claim or exhibit is hovered or focused: provenance edge and tint
 *   selected  this exhibit is open: marker fill, ink edge (aria-pressed on a button)
 *   withheld  no number (evidence not cited, or kept from the model): an en dash
 *   skipped   a trace stop that didn't run: hatched
 *   failed    a trace stop that failed: fault, with a cross instead of the number
 *
 * As a button it needs an accessible name ("Citation 3: HAL-4821, 14 Aug 2026"); as a
 * static mark it is decorative unless given a label.
 */
export type ExhibitState = "rest" | "selected" | "skipped" | "failed";

interface Common {
  /** The exhibit number; null for an un-numbered (withheld) item. */
  n: number | null;
  size?: "inline" | "lg";
  state?: ExhibitState;
  linked?: boolean;
}

function classes({ n, size = "inline", state = "rest", linked = false }: Common, extra?: string) {
  return [styles.marker, size === "lg" && styles.lg, state !== "rest" && styles[state], linked && styles.linked, n === null && styles.withheld, extra]
    .filter(Boolean)
    .join(" ");
}

function Face({ n, state }: { n: number | null; state?: ExhibitState }) {
  if (state === "failed") return <X size={12} strokeWidth={2} aria-hidden="true" focusable="false" />;
  return <>{n ?? "–"}</>;
}

/** A static marker. Decorative by default; pass `label` when it stands alone. */
export function ExhibitMarker({ label, className, ...common }: Common & { label?: string; className?: string }) {
  return (
    <span className={classes(common, className)} aria-hidden={label ? undefined : true} aria-label={label} role={label ? "img" : undefined}>
      <Face n={common.n} state={common.state} />
    </span>
  );
}

export interface ExhibitButtonProps extends Omit<Common, "state">, Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  selected?: boolean;
  "aria-label": string;
}

/** An interactive marker: opens its exhibit. Selection is announced with aria-pressed. */
export const ExhibitButton = forwardRef<HTMLButtonElement, ExhibitButtonProps>(function ExhibitButton(
  { n, size, linked, selected = false, className, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={classes({ n, size, linked, state: selected ? "selected" : "rest" }, [styles.interactive, className].filter(Boolean).join(" "))}
      aria-pressed={selected}
      {...rest}
    >
      <Face n={n} />
    </button>
  );
});
