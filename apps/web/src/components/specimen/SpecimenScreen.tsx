"use client";

import { Check, GitFork, Lock, X } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { Mark } from "@/components/brand/Mark";
import { Wordmark } from "@/components/brand/Wordmark";
import { Cluster, Inline, MarginGrid, Page, Split, Stack } from "@/components/layout/Layout";
import { Button } from "@/components/primitives/Button";
import { LATTICE_GLYPHS, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { Tag } from "@/components/primitives/Tag";
import { CONTRAST_PAIRS, contrast } from "@/styles/contrast-pairs";

import styles from "./Specimen.module.css";

type Theme = "light" | "dark";

const ROLES: readonly { role: string; voice: string; spec: string; sample: string }[] = [
  { role: "display", voice: "Record", spec: "44/48 · 500 (34/40 < 768)", sample: "What did we promise Acme about SCIM?" },
  { role: "lead", voice: "Record", spec: "21/32 · 450 (19/28 < 768)", sample: "A decision record dated 18 Sep moves SCIM general availability to December, with a November beta for Acme's pilot group." },
  { role: "prose", voice: "Record", spec: "17/28 · 400 · max 66ch", sample: "At the June kickoff, Halden told Acme it was targeting SCIM provisioning in Q3. On 14 Aug, Support told Acme that SCIM was expected to be generally available in October." },
  { role: "title", voice: "Interface", spec: "26/32 · 650 (22/28 < 768)", sample: "Change Maya Okafor's access" },
  { role: "subtitle", voice: "Interface", spec: "18/24 · 650", sample: "DR-112 · Identity roadmap decision" },
  { role: "heading", voice: "Interface", spec: "15/22 · 600", sample: "Acme kickoff — call notes" },
  { role: "body", voice: "Interface", spec: "14/20 · 400", sample: "Every change is previewed first, needs a reason, and is recorded in the audit log with your name." },
  { role: "body-strong", voice: "Interface", spec: "14/20 · 600", sample: "Maya Okafor will gain access to Kestrel Bank content." },
  { role: "small", voice: "Interface", spec: "13/18 · 400", sample: "Counted only among documents you can read." },
  { role: "section", voice: "Interface", spec: "13/18 · 600", sample: "What the evidence doesn't establish" },
  { role: "caption", voice: "Interface", spec: "12/16 · 400", sample: "Recorded in the audit log with your name. Don't paste document content." },
  { role: "meta", voice: "Ledger", spec: "12/16 · 400 · tabular", sample: "HAL-4821 · 18 Sep 2026 · 4.2 s · rec_3f9a0c1d2e4b5a67" },
  { role: "mark", voice: "Ledger", spec: "11/14 · 500 · uppercase +0.06em — status keyword and exhibit tags only", sample: "Answered · 3 sources cited · newest 18 Sep 2026" },
  { role: "code", voice: "Ledger", spec: "13/20 · 400", sample: '{ "stage": "claim_validation", "code": "quote_not_found" }' },
];

const SURFACES = ["--surface-paper", "--surface-sheet", "--surface-well", "--surface-well-strong", "--surface-inverse"];
const TEXTS = ["--text-primary", "--text-secondary", "--text-muted"];
const RULES = ["--rule", "--rule-strong", "--rule-ink", "--focus-ring", "--boundary-edge"];
const SIGNALS = [
  ["provenance", "Citations, trails, focus, the lit cell"],
  ["passage", "The exact verified quote"],
  ["disagreement", "Potential conflict, supersession"],
  ["time", "Stale evidence, old syncs, waits"],
  ["fault", "Errors, failed syncs"],
  ["healthy", "Sync succeeded (always with a check)"],
] as const;
const SPACES = ["0-5", "1", "2", "3", "4", "5", "6", "8", "10", "12", "16"];
const MOTION = [
  ["--motion-quick", "Hover, press"],
  ["--motion-base", "Marker sweep, popovers"],
  ["--motion-arrive", "Brief arrives (fade + 4 px rise)"],
  ["--motion-panel", "Inspector opens"],
  ["--motion-stagger", "Delay between arriving parts"],
];

export function SpecimenScreen() {
  const [theme, setTheme] = useState<Theme>("light");
  return (
    <div className={styles.root} data-theme={theme}>
      <main id="main">
        <Page width="admin" className={styles.page}>
          <header className={styles.head}>
            <Stack space="3">
              <Cluster space="3">
                <Wordmark />
                <Tag tone="quiet">Development only</Tag>
              </Cluster>
              <h1 className={styles.title}>Specimen</h1>
              <p className={styles.deck}>
                Every token, type role, glyph and layout primitive of design-system v2 (design-direction §4). Light is the
                shipped theme; the dark map exists only so the system is ready for it and is never offered to people.
              </p>
            </Stack>
            <div className={styles.themeSwitch} role="group" aria-label="Theme map shown on this page">
              <Button size="sm" variant={theme === "light" ? "primary" : "secondary"} aria-pressed={theme === "light"} onClick={() => setTheme("light")}>
                Light (shipped)
              </Button>
              <Button size="sm" variant={theme === "dark" ? "primary" : "secondary"} aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>
                Dark map
              </Button>
            </div>
          </header>

          <Section n="01" title="Fonts" note="Self-hosted from /fonts (scripts/build-fonts.py). Latin files are preloaded; Latin Extended loads only when a page uses it.">
            <FontStatus />
          </Section>

          <Section n="02" title="Type roles" note="Three voices. The record never appears in controls; the interface never sets answer prose.">
            <ol className={styles.roles}>
              {ROLES.map((r) => (
                <li key={r.role} className={styles.role} data-testid={`role-${r.role}`}>
                  <div className={styles.roleMeta}>
                    <span className={styles.roleName}>{r.role}</span>
                    <span className={styles.roleSpec}>
                      {r.voice} · {r.spec}
                    </span>
                  </div>
                  <p className={`t-${r.role} ${styles.roleSample}`}>{r.sample}</p>
                </li>
              ))}
            </ol>
            <div className={styles.checks}>
              <Check2 label="Distinct shapes (ledger)" className="t-meta">
                Il1| O0 rn m · 0O 1lI 5S 8B
              </Check2>
              <Check2 label="Distinct shapes (interface)" className="t-body">
                Il1| O0 rn m · Illinois 1001 · Ollie
              </Check2>
              <Check2 label="Tabular figures" className="t-meta">
                <span className={styles.tabular}>
                  <span>1,111.11</span>
                  <span>8,808.08</span>
                  <span>42.40 s</span>
                </span>
              </Check2>
              <Check2 label="Latin Extended (downloads on use)" className="t-prose">
                Łukasz Żółć · Dvořák · Şükrü Çağlar · Ångström · Ærø
              </Check2>
              <Check2 label="Long unbroken name" className="t-heading">
                <span className={styles.wrapAnywhere}>Northpeak-Logistics-International-Holdings-EMEA-Operations-Group</span>
              </Check2>
            </div>
          </Section>

          <Section n="03" title="Colour" note="Paper, ink and five signals. Ratios are measured live from the rendered colours of this theme map.">
            <h3 className={styles.sub}>Surfaces and text</h3>
            <div className={styles.matrixWrap}>
              <table className={styles.matrix}>
                <caption className="visually-hidden">Text tokens on each surface, with measured contrast</caption>
                <thead>
                  <tr>
                    <th scope="col">Surface</th>
                    {TEXTS.map((t) => (
                      <th key={t} scope="col" className={styles.mono}>
                        {t}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {SURFACES.filter((s) => s !== "--surface-inverse").map((s) => (
                    <tr key={s}>
                      <th scope="row" className={styles.mono}>
                        {s}
                      </th>
                      {TEXTS.map((t) => (
                        <td key={t}>
                          <Swatch fg={t} bg={s} />
                        </td>
                      ))}
                    </tr>
                  ))}
                  <tr>
                    <th scope="row" className={styles.mono}>
                      --surface-inverse
                    </th>
                    <td colSpan={3}>
                      <Swatch fg="--text-inverse" bg="--surface-inverse" />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h3 className={styles.sub}>Signals: one job each</h3>
            <ul className={styles.signals}>
              {SIGNALS.map(([s, job]) => (
                <li key={s} className={styles.signal}>
                  {s === "passage" ? (
                    <Swatch fg="--signal-passage-text" bg="--signal-passage-fill" label="Selected marker" />
                  ) : (
                    <Swatch fg={`--signal-${s}-text`} bg={`--signal-${s}-tint`} label={`${s} text on tint`} />
                  )}
                  <p className={styles.signalName}>{s}</p>
                  <p className={styles.signalJob}>{job}</p>
                </li>
              ))}
              <li className={styles.signal}>
                <div className={`hatch ${styles.hatchSwatch}`} aria-hidden="true" />
                <p className={styles.signalName}>boundary</p>
                <p className={styles.signalJob}>Not crossed. A texture with a dashed edge, never a colour.</p>
              </li>
            </ul>

            <h3 className={styles.sub}>Rules</h3>
            <ul className={styles.rules}>
              {RULES.map((r) => (
                <li key={r}>
                  <span className={styles.ruleLine} style={{ borderTopColor: `var(${r})` }} aria-hidden="true" />
                  <span className={styles.mono}>{r}</span>
                </li>
              ))}
            </ul>

            <h3 className={styles.sub}>Every pairing the system relies on</h3>
            <ContrastLedger />
          </Section>

          <Section n="04" title="Mark and glyphs" note="Lucide for general UI; Lattice glyphs for the product's own concepts. 16 px grid, 1.5 px stroke.">
            <Cluster space="8" align="end">
              {([32, 24, 20, 16] as const).map((size) => (
                <Stack key={size} space="2" className={styles.center}>
                  <Mark size={size} />
                  <span className={styles.caption}>{size}</span>
                </Stack>
              ))}
              <Wordmark />
              <Wordmark size={16} />
            </Cluster>
            <ul className={styles.glyphs}>
              {Object.entries({ ...LATTICE_GLYPHS, ...SOURCE_GLYPHS }).map(([name, G]) => (
                <li key={name} className={styles.glyph}>
                  <Cluster space="3">
                    <Icon as={G} size={16} />
                    <Icon as={G} size={20} />
                    <Icon as={G} size={32} />
                  </Cluster>
                  <span className={styles.caption}>{name}</span>
                </li>
              ))}
              <li className={styles.glyph}>
                <Cluster space="3">
                  <Icon as={GitFork} />
                  <Icon as={Lock} />
                  <Icon as={Check} />
                </Cluster>
                <span className={styles.caption}>Lucide beside them</span>
              </li>
            </ul>
            <Cluster space="2">
              <Tag icon={<Icon as={SOURCE_GLYPHS.doc} size={12} />}>Document</Tag>
              <Tag icon={<Icon as={SOURCE_GLYPHS.ticket} size={12} />}>Ticket</Tag>
              <Tag icon={<Icon as={SOURCE_GLYPHS.chat} size={12} />}>Chat</Tag>
              <Tag icon={<Icon as={Lock} size={12} />}>Confidential</Tag>
              <Tag tone="inverse" icon={<Icon as={Lock} size={12} />}>
                Restricted
              </Tag>
            </Cluster>
          </Section>

          <Section n="05" title="Shape, elevation, focus" note="Radii 2 / 4 / 8. One elevation, for floating layers only. Focus is a 2 px provenance ring.">
            <Cluster space="6" align="start">
              {["--radius-xs", "--radius-sm", "--radius-md"].map((r) => (
                <Stack key={r} space="2">
                  <span className={styles.radius} style={{ borderRadius: `var(${r})` }} aria-hidden="true" />
                  <span className={styles.caption}>{r}</span>
                </Stack>
              ))}
              <Stack space="2">
                <span className={styles.elevated} aria-hidden="true" />
                <span className={styles.caption}>--elevation-overlay</span>
              </Stack>
            </Cluster>
            <Cluster space="3">
              <Button variant="primary">Apply change</Button>
              <Button>Back</Button>
              <Button variant="ghost" icon={<Icon as={X} />} aria-label="Close" />
              <Button loading>Syncing</Button>
              <Button disabled>Disabled</Button>
              <span className={styles.caption}>Tab through these to see the focus ring.</span>
            </Cluster>
          </Section>

          <Section n="06" title="Space and motion" note="4 px base. Motion explains a relationship or it doesn't happen; reduced motion makes every duration 0.">
            <Split ratio="1:1" below="1024">
              <ul className={styles.spaces}>
                {SPACES.map((s) => (
                  <li key={s}>
                    <span className={styles.mono}>--space-{s}</span>
                    <span className={styles.spaceBar} style={{ width: `var(--space-${s})` }} aria-hidden="true" />
                  </li>
                ))}
              </ul>
              <MotionTable />
            </Split>
          </Section>

          <Section n="07" title="Layout primitives" note="Stack, Cluster, Inline, Split, Page and the margin grid. Spacing is a gap on the parent, never a margin on the child.">
            <div className={styles.demo}>
              <p className={styles.demoLabel}>MarginGrid: exhibit numbers in the margin, sidenotes in the notes column (≥ 1440 px)</p>
              <Stack space="3">
                <MarginGrid margin={<span className={styles.exhibit}>1</span>} notes={<span className="t-meta">DR-112 · 18 Sep 2026</span>}>
                  <p className="t-lead">A decision record dated 18 Sep moves SCIM general availability to December.</p>
                </MarginGrid>
                <MarginGrid
                  margin={
                    <>
                      <span className={styles.exhibit}>2</span>
                      <span className={styles.exhibit}>3</span>
                    </>
                  }
                  notes={<span className="t-meta">Kickoff notes · HAL-4821</span>}
                >
                  <p className="t-prose">At the June kickoff, Halden told Acme it was targeting Q3. On 14 Aug, Support told Acme October.</p>
                </MarginGrid>
              </Stack>
            </div>
            <div className={styles.demo}>
              <p className={styles.demoLabel}>Inline: label · shrinking value · action</p>
              <Inline space="3">
                <span className="t-section">Source</span>
                <span className={styles.truncate}>Halden Support (Zendesk) — a very long source name that truncates rather than wrapping</span>
                <Button size="sm">Sync now</Button>
              </Inline>
            </div>
            <div className={styles.demo}>
              <p className={styles.demoLabel}>Split 5:7, stacking below 1024 px</p>
              <Split ratio="5:7">
                <div className={styles.block}>5</div>
                <div className={styles.block}>7</div>
              </Split>
            </div>
          </Section>

          <Section n="08" title="Responsive type" note="Measured from this page as rendered at the current viewport width.">
            <ResponsiveReadout />
          </Section>
        </Page>
      </main>
    </div>
  );
}

function Section({ n, title, note, children }: { n: string; title: string; note: string; children: ReactNode }) {
  return (
    <section className={styles.section} aria-labelledby={`specimen-${n}`}>
      <div className={styles.sectionHead}>
        <span className={styles.sectionNumber}>{n}</span>
        <h2 id={`specimen-${n}`} className={styles.sectionTitle}>
          {title}
        </h2>
        <p className={styles.sectionNote}>{note}</p>
      </div>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}

function Check2({ label, className, children }: { label: string; className: string; children: ReactNode }) {
  return (
    <div className={styles.check}>
      <span className={styles.checkLabel}>{label}</span>
      <p className={className}>{children}</p>
    </div>
  );
}

function rgb(value: string): [number, number, number] | null {
  const m = /rgba?\(([\d.]+),?\s*([\d.]+),?\s*([\d.]+)/.exec(value);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** Measures the rendered pair, so the number is the theme map's real value. */
function useRatio(fg: string, bg: string) {
  const ref = useRef<HTMLSpanElement>(null);
  const [ratio, setRatio] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const style = getComputedStyle(el);
      const a = rgb(style.color);
      const b = rgb(style.backgroundColor);
      setRatio(a && b ? contrast(a, b) : null);
    };
    measure();
    const observer = new MutationObserver(measure);
    const root = el.closest("[data-theme]");
    if (root) observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [fg, bg]);
  return { ref, ratio };
}

function Swatch({ fg, bg, label }: { fg: string; bg: string; label?: string }) {
  const { ref, ratio } = useRatio(fg, bg);
  return (
    <span ref={ref} className={styles.swatch} style={{ color: `var(${fg})`, backgroundColor: `var(${bg})` }}>
      <span>{label ?? "Aa Text"}</span>
      <span className={styles.swatchRatio}>{ratio ? `${ratio.toFixed(2)}:1` : "…"}</span>
    </span>
  );
}

function ContrastRow({ fg, bg, min, use }: { fg: string; bg: string; min: number; use: string }) {
  const { ref, ratio } = useRatio(fg, bg);
  const pass = ratio !== null && ratio >= min;
  return (
    <tr>
      <td>
        {/* 4.5:1 pairs are text; 3:1 pairs are boundaries and graphics, so they're drawn as a rule. */}
        <span ref={ref} className={styles.pairChip} style={{ color: `var(${fg})`, backgroundColor: `var(${bg})` }} aria-hidden={min === 3 || undefined}>
          {min === 3 ? <span className={styles.pairRule} /> : "Aa"}
        </span>
      </td>
      <td className={styles.mono}>{fg}</td>
      <td className={styles.mono}>{bg}</td>
      <td>{use}</td>
      <td className={styles.num}>{ratio ? ratio.toFixed(2) : "…"}</td>
      <td className={styles.num}>{min}</td>
      <td>
        <span className={pass ? styles.pass : styles.fail}>
          <Icon as={pass ? Check : X} size={14} />
          {pass ? "Pass" : "Fail"}
        </span>
      </td>
    </tr>
  );
}

function ContrastLedger() {
  return (
    <div className={styles.matrixWrap}>
      <table className={styles.ledger} data-testid="contrast-ledger">
        <caption className="visually-hidden">Measured contrast of every colour pairing</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Sample</span>
            </th>
            <th scope="col">Foreground</th>
            <th scope="col">Background</th>
            <th scope="col">Used for</th>
            <th scope="col" className={styles.num}>
              Ratio
            </th>
            <th scope="col" className={styles.num}>
              Minimum
            </th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {CONTRAST_PAIRS.map((p) => (
            <ContrastRow key={`${p.fg}|${p.bg}`} {...p} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FontStatus() {
  const [faces, setFaces] = useState<{ family: string; weight: string; status: string; range: string }[]>([]);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  useEffect(() => {
    let live = true;
    void document.fonts.ready.then(() => {
      if (!live) return;
      setFaces(
        [...document.fonts]
          .filter((f) => /^Lattice (Record|Interface|Ledger)$/.test(f.family.replace(/"/g, "")))
          // Browsers normalise "U+0000-00FF" to "U+0-FF"; only the Latin face starts there.
          .map((f) => ({ family: f.family.replace(/"/g, ""), weight: f.weight, status: f.status, range: /^U\+0+-0*FF\b/i.test(f.unicodeRange) ? "Latin" : "Latin Extended" })),
      );
      setChecks(
        Object.fromEntries(
          ["Lattice Record", "Lattice Interface", "Lattice Ledger"].map((family) => [family, document.fonts.check(`16px "${family}"`)]),
        ),
      );
    });
    return () => {
      live = false;
    };
  }, []);
  return (
    <div className={styles.matrixWrap}>
      <table className={styles.ledger} data-testid="font-status">
        <caption className="visually-hidden">Font faces and their load status</caption>
        <thead>
          <tr>
            <th scope="col">Family</th>
            <th scope="col">Weight</th>
            <th scope="col">Subset</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {faces.map((f) => (
            <tr key={`${f.family}-${f.weight}-${f.range}`}>
              <td>
                <span style={{ fontFamily: `"${f.family}"` }}>{f.family}</span>
              </td>
              <td className={styles.mono}>{f.weight}</td>
              <td>{f.range}</td>
              <td className={styles.mono} data-status={f.status}>
                {f.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.caption} data-testid="font-checks">
        {Object.entries(checks)
          .map(([family, ok]) => `${family}: ${ok ? "ready" : "not ready"}`)
          .join(" · ")}
        {" "}· Fallback faces (Georgia, Arial, Courier New, metric-matched) render only if a web font fails.
      </p>
    </div>
  );
}

function subscribeToMotionPreference(onChange: () => void) {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function MotionTable() {
  // Read from the live document (reduced motion changes the values); nothing on the server.
  const snapshot = useSyncExternalStore(
    subscribeToMotionPreference,
    () => MOTION.map(([t]) => getComputedStyle(document.documentElement).getPropertyValue(t!).trim()).join("|"),
    () => "",
  );
  const values: Record<string, string> = snapshot ? Object.fromEntries(MOTION.map(([t], i) => [t!, snapshot.split("|")[i] ?? ""])) : {};
  return (
    <table className={styles.ledger}>
      <caption className="visually-hidden">Motion tokens</caption>
      <thead>
        <tr>
          <th scope="col">Token</th>
          <th scope="col">Now</th>
          <th scope="col">Use</th>
        </tr>
      </thead>
      <tbody>
        {MOTION.map(([t, use]) => (
          <tr key={t}>
            <td className={styles.mono}>{t}</td>
            <td className={styles.mono}>{values[t!] ?? "…"}</td>
            <td>{use}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ResponsiveReadout() {
  const [rows, setRows] = useState<[string, string][]>([]);
  useEffect(() => {
    const measure = () => {
      const read = (role: string) => {
        const el = document.querySelector<HTMLElement>(`[data-testid="role-${role}"] p`);
        if (!el) return "—";
        const s = getComputedStyle(el);
        return `${s.fontSize} / ${s.lineHeight} · ${s.fontFamily.split(",")[0]}`;
      };
      setRows([
        ["Viewport", `${window.innerWidth} px`],
        ["Gutter", getComputedStyle(document.documentElement).getPropertyValue("--gutter").trim()],
        ...["display", "title", "lead", "prose", "body", "meta"].map((r) => [r, read(r)] as [string, string]),
      ]);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return (
    <dl className={styles.readout} data-testid="responsive-readout">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd className={styles.mono}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
