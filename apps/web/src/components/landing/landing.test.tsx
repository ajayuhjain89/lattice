import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CLAIMS, CONFLICT_POSITIONS, ESCALATION, EXHIBITS } from "./example";
import { Landing } from "./Landing";

const HALDEN = resolve(__dirname, "../../../../../data/synthetic/halden");

function corpus(): string {
  const texts: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(md|json|jsonl|yaml)$/.test(name)) texts.push(readFileSync(path, "utf8"));
    }
  };
  walk(HALDEN);
  // Compare as prose: markdown emphasis and line breaks removed, JSON newlines unescaped.
  return texts.join("\n").replaceAll("\\n", " ").replaceAll("**", "").replace(/\s+/g, " ");
}

const flat = (s: string) => s.replace(/\s+/g, " ").replace(/ …$/, "").replace(/; …$/, "").trim();

function renderLanding() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Landing />
    </QueryClientProvider>,
  );
}

describe("the landing example", () => {
  const text = corpus();

  it.each(Object.values(EXHIBITS).map((e) => [e.title, e] as const))("quotes %s verbatim from the synthetic corpus", (_, e) => {
    expect(text).toContain(flat(e.passage.quote));
    expect(text).toContain(flat(`${e.passage.before}${e.passage.quote}${e.passage.after}`));
  });

  it("names only synthetic Halden material", () => {
    expect(text).toContain(ESCALATION.title.replace("—", "—"));
    expect(text).toContain("sensitivity: confidential");
    for (const e of Object.values(EXHIBITS)) expect(text).toContain(e.author);
  });

  it("cites only exhibits that exist, and the conflict uses the same exhibits", () => {
    const numbers = new Set(Object.values(EXHIBITS).map((e) => e.n));
    for (const c of CLAIMS) for (const n of c.cites) expect(numbers.has(n)).toBe(true);
    for (const p of CONFLICT_POSITIONS) expect(numbers.has(p.exhibit.n)).toBe(true);
  });
});

describe("Landing", () => {
  it("renders without a session and makes no request", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderLanding();
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("has one h1, ordered section headings and the real CTA only", () => {
    renderLanding();
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Answers you can follow back to the page."]);
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "It searches only what you can read",
      "Every claim keeps its evidence",
      "Disagreement stays on the page",
      "Boundaries are drawn, not hidden",
      "Access you can explain",
      "Sign in through your organization.",
    ]);
    const ctas = screen.getAllByRole("link", { name: /sign in/i });
    expect(ctas.length).toBeGreaterThan(0);
    for (const link of ctas) expect(link).toHaveAttribute("href", "/sign-in");
    expect(document.body).not.toHaveTextContent(/free trial|book a demo|contact sales|pricing|testimonial|trusted by|SOC 2|ISO 27001/i);
  });

  it("labels every example as synthetic Halden material", () => {
    renderLanding();
    expect(screen.getAllByText(/synthetic company Halden/).length).toBeGreaterThanOrEqual(5);
  });

  it("opens an exhibit's passage from its claim", async () => {
    renderLanding();
    // Opens on exhibit 2 (the hero already shows exhibit 1).
    const viewer = screen.getByRole("region", { name: "Exhibit 2" });
    expect(within(viewer).getByText(EXHIBITS.ticket.passage.quote).tagName).toBe("MARK");
    const kickoff = screen.getByRole("button", { name: /^Exhibit 3: Halden Drive/ });
    expect(kickoff).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(kickoff);
    expect(kickoff).toHaveAttribute("aria-pressed", "true");
    const shown = screen.getByRole("region", { name: "Exhibit 3" });
    expect(within(shown).getByText(EXHIBITS.kickoff.passage.quote).tagName).toBe("MARK");
    expect(screen.getByRole("status")).toHaveTextContent(`Showing exhibit 3: ${EXHIBITS.kickoff.title}`);
  });

  it("draws the egress lattice from the example policy, hatched where nothing is sent", () => {
    renderLanding();
    const table = screen.getByRole("table", { name: /Which data levels each model may receive/ });
    expect(within(table).getByText("Confidential data isn't sent to the external model API")).toBeInTheDocument();
    expect(within(table).getByText("Confidential data may be sent to Halden's own model server")).toBeInTheDocument();
    expect(within(table).getByText("Restricted data isn't sent to Halden's own model server")).toBeInTheDocument();
  });
});
