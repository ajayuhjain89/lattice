/**
 * The token system's guarantees (design-direction §4.6), checked from the CSS itself:
 * contrast in both theme maps, every var() resolving, palette tokens never leaking into
 * components, and no design-system v1 names surviving.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { CONTRAST_PAIRS, contrast } from "./contrast-pairs";

const STYLES = join(__dirname);
const SRC = join(__dirname, "..");
const tokens = readFileSync(join(STYLES, "tokens.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const globals = readFileSync(join(STYLES, "globals.css"), "utf8");

/** Declarations of one top-level block whose selector list includes `selector`. */
function block(selector: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const match of tokens.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1]!.split(",").map((s) => s.trim());
    if (!selectors.includes(selector)) continue;
    for (const decl of match[2]!.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) out.set(decl[1]!, decl[2]!.trim());
  }
  return out;
}

const palette = block(":root");
const themes = { light: block('[data-theme="light"]'), dark: block('[data-theme="dark"]') };

function hex(value: string): [number, number, number] {
  const h = value.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}

function resolve(theme: Map<string, string>, name: string, depth = 0): string {
  const value = theme.get(name) ?? palette.get(name);
  if (value === undefined) throw new Error(`${name} is not defined`);
  const ref = /^var\((--[a-z0-9-]+)\)$/.exec(value);
  if (ref && depth < 5) return resolve(theme, ref[1]!, depth + 1);
  return value;
}

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "api" ? [] : files(path);
    return /\.(css|tsx?)$/.test(name) ? [path] : [];
  });
}

describe("contrast (WCAG 2.2: 4.5:1 text, 3:1 boundaries and graphics)", () => {
  for (const [name, theme] of Object.entries(themes)) {
    it(`every pairing passes in the ${name} map`, () => {
      const failures = CONTRAST_PAIRS.map((p) => ({ ...p, ratio: contrast(hex(resolve(theme, p.fg)), hex(resolve(theme, p.bg))) }))
        .filter((p) => p.ratio < p.min)
        .map((p) => `${p.fg} on ${p.bg}: ${p.ratio.toFixed(2)} < ${p.min} (${p.use})`);
      expect(failures).toEqual([]);
    });
  }

  it("both maps define the same semantic tokens", () => {
    expect([...themes.dark.keys()].sort()).toEqual([...themes.light.keys()].sort());
  });
});

describe("token usage", () => {
  const defined = new Set([...tokens.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]!));
  const sources = files(SRC).filter((f) => !f.endsWith("tokens.css") && !f.endsWith(".test.ts"));

  it("every var() resolves to a token or a property the same file defines", () => {
    const unresolved = sources.flatMap((file) => {
      const text = readFileSync(file, "utf8");
      // Properties a file defines, or that its sibling components set as inline styles.
      const siblings = readdirSync(dirname(file)).filter((n) => /\.tsx$/.test(n)).map((n) => readFileSync(join(dirname(file), n), "utf8"));
      const local = new Set([
        ...[...text.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]!),
        ...siblings.flatMap((t) => [...t.matchAll(/"(--[a-z0-9-]+)"\s*:/g)].map((m) => m[1]!)),
      ]);
      return [...text.matchAll(/var\((--[a-z0-9-]+)(?![-a-z0-9]*\$)/g)]
        .map((m) => m[1]!)
        .filter((v) => !v.endsWith("-"))
        // Radix sets --radix-* on its own elements at runtime (toast swipe, popover origin).
        .filter((v) => !v.startsWith("--radix-"))
        .filter((v) => !defined.has(v) && !local.has(v))
        .map((v) => `${relative(SRC, file)}: ${v}`);
    });
    expect(unresolved).toEqual([]);
  });

  it("components use semantic tokens, never the palette", () => {
    const leaks = sources.filter((f) => /var\(--p-/.test(readFileSync(f, "utf8"))).map((f) => relative(SRC, f));
    expect(leaks).toEqual([]);
  });

  it("no design-system v1 name survives", () => {
    const v1 = /--(?:bg|accent|status)-|--border-(?:default|strong|focus)\b|--font-(?:sans|mono|geist)|--type-(?:h[123]|label)-|--shadow-overlay|--motion-(?:fast|result)\b|\bt-(?:h[123]|label)\b|radius-(?:lg|full)\b/;
    const hits = [...sources, join(STYLES, "tokens.css")].filter((f) => v1.test(readFileSync(f, "utf8"))).map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("every composed type role exists in globals.css", () => {
    const roles = new Set([...globals.matchAll(/^\.(t-[a-z-]+)\s*\{/gm)].map((m) => m[1]!));
    const missing = sources.flatMap((f) =>
      [...readFileSync(f, "utf8").matchAll(/composes: (t-[a-z-]+) from global/g)].map((m) => m[1]!).filter((r) => !roles.has(r)),
    );
    expect(missing).toEqual([]);
  });
});
