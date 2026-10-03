# Lattice — Design Direction (product-design overhaul)

Status: **Accepted (D0, 2026-10-02)** — the locked direction for this overhaul and the
source of truth for its implementation (D1–D7). It is not reopened while the overhaul is in
progress; the system is judged and refined only once it is implemented across the product.
On completion (D7), `design-system.md` is rewritten as v2 to describe the implemented system,
and Parts C–F of the old system are retired.
Scope: the web app (`apps/web`) and a new public landing page. Frontend only — no API,
schema or authorization change; if an existing API deficiency makes an approved UX
impossible, implementation stops and reports it instead of inventing frontend data.
Phase 11 is not started.
Owner: Product design / product engineering. Drafted: 2026-10-02.

---

## 1. Audit of the current frontend

Inspected: every route and component in `apps/web/src`, `tokens.css`, `globals.css`, the
screenshot harness output (31 screens × 5 widths), `design-system.md` Parts A–F,
`product-brief.md`, `security-model.md` §1 and §10, and `implementation-plan.md` Phases 7–10.

### 1.1 What is right and must survive any redesign

These are product decisions, not styling. The redesign must keep all of them:

- **Answers read as a brief, not a chat.** The composer sits at the top as a research query;
  there are no bubbles, avatars or personas.
- **The trust copy.** "Lattice can't confirm which is current", "you are authorized — this
  is a policy about the model, not about you", "Lattice doesn't say which": the words carry
  the security model and are among the best parts of the product.
- **Every state is designed** (B13). This covers 13 Ask states plus the rate-limit, identity
  and not-available pages, all with fixtures.
- **Honest progress.** There are no invented pipeline stages (D-6 is still open).
- **The security rules in the UI.** SEC-5 (unreadable = nonexistent), SEC-14 (no
  side-channel enumeration), SEC-15 (admin ≠ content) and D-8 (no substitute account
  names) are enforced in components and tested.
- **The engineering discipline.** Token-only colour (Stylelint), CSS Modules, Radix for
  focus management, axe on every state, geometry assertions at five widths, `en-GB`
  absolute dates and tabular figures.

### 1.2 Why the current UI doesn't reach the bar

| # | Finding | Evidence |
| - | ------- | -------- |
| 1 | **The identity is borrowed.** The palette, keylines, mono labels and pill tags were measured from a third party's portfolio handout (`docs/references/FDE_Portfolio_…pdf`, design-system Part A). Lime/lavender/pink belong to that document, not to permission-aware evidence. Take the logo away and the screens read as "tasteful developer handout", not Lattice. | `tokens.css` accents; Part A2 |
| 2 | **Geist is the default typeface of a generation of AI SaaS products.** Answers, tables and headings share one neutral grotesque, so an answer looks like UI chrome instead of a document you can quote. | `layout.tsx` |
| 3 | **The mono uppercase label is everywhere** (20 module uses, plus every table header, crumb, status line and section). When everything is a label, nothing is. It makes the app shout in small caps. | `t-label` |
| 4 | **Colour meanings collide.** Lavender is links, citations, focus, info notices *and* the sign-in headline. Lime is the selected citation, the cited passage, stage markers *and* success. A pink tint fills the potential-conflict callout, the loudest object on the page — for information the product says is *not* a failure. | Part B2; `ask-answered@1440` |
| 5 | **The signature ideas are invisible.** Permission boundaries, AI egress, provenance and verification are Lattice's whole value, yet they appear only as sentences and small tags. Nothing visual is unique to Lattice. | All screens |
| 6 | **Chrome spends space the work needs.** A 240 px sidebar holds 3–5 links. The 56 px page header shows one mono word ("ASK"). At 1280 px, nav + reading column + inspector leave the answer about 600 px. | `AppShell`, `PageHeader` |
| 7 | **Navigation is shallow, and inconsistent between widths.** "This session" history lives in tab memory although `/v1/activity` exists (Phase 8). There is no way to jump to a member, a source or a past question without the mouse. The rail (768–1279) hides labels. | `Sidebar.tsx`, `nav.ts` |
| 8 | **Each data surface is its own one-off.** There are four tables (Sources, Activity, Access, Providers), three input styles (sign-in, scope filter, access search) and seven separate "stack below 1024 px" rules. The `Stack`/`Cluster` layout primitives promised in B16 were never built, and 162 raw px values live in component CSS. | grep of `src/components` |
| 9 | **The evidence interaction stops at "open a panel".** There is no view of how claims and sources relate across the whole answer: you open citations one at a time. The inspector's metadata is a generic definition list; provenance (source → document → version → passage → verified quote) is never shown as a chain. | `EvidenceBody.tsx` |
| 10 | **Admin surfaces explain in prose what a diagram would show at a glance.** The egress policy ("Up to Internal", "Not allowed") and member access ("clearance, scope, departments, groups, grants") are inherently two-dimensional, but they are rendered as lists. | `providers@1440`, `access-member@1440` |
| 11 | **There is no public face.** `/` redirects to `/ask`, and sign-in is a two-column page that repeats the Ask intro's three "principles" verbatim in different words. | `page.tsx`, `SignIn.tsx`, `AskIntro.tsx` |
| 12 | **Empty, loading and error states are correct but anonymous.** They are generic blocks that don't feel like the same product as the answer. | `States.module.css`, `NotAvailable` |

(The harness screenshots in `e2e/screens/` predate the Phase 9 review closure: the member
page still shows a private channel's reference and member list. Current code doesn't render
them, and the screens are regenerated in step D7.)

---

## 2. The idea

**Lattice shows its work, and shows its edges.**

Every other knowledge tool hides two things: *where* a statement came from, and *what it
couldn't use*. Lattice's product exists to make both visible. So the visual system is built
from two recurring forms:

1. **The thread: provenance made visible.** Claims, exhibits, passages and traces are
   connected by numbered **exhibit markers** and drawn **provenance trails**. Anything
   Lattice says can be followed back to its source in one gesture.
2. **The edge: boundaries made visible.** What sits outside a boundary — content the AI data
   policy kept from the model, a page you can't open, a read-only setting — is drawn with
   one texture: **boundary hatching**. Hatching is Lattice's visual word for "not crossed".

These forms meet in the **lattice**: a grid of rows and columns where filled cells are the
relationships that hold. Lattice draws real relationships this way, and only real ones:

| Lattice | Rows × columns | Where |
| ------- | -------------- | ----- |
| Provenance lattice | claims × exhibits (cell = this claim cites this exhibit) | Ask brief |
| Egress lattice | data levels (Internal / Confidential / Restricted) × providers (+ "stays in Lattice") | AI providers |
| Access lattice | levels × reach (accounts, departments, groups, grants) for one member | Access → member |
| Brand mark | 3 × 3 crossing lines, one cell lit | Everywhere |

That is the logo test. A screenshot shows marginal exhibit numbers, hatched edges, a quiet
paper ground with ink type, a serif answer and a small lattice of filled cells — and none of
those appear together in any other product.

### What this direction refuses

- No gradients, glass, glows, blobs or "AI sparkle" iconography. Lattice is not magic. It
  is checked.
- No card grids. Surfaces are ruled paper: rows, rules and margins. A container exists only
  for state, interaction or a boundary (keeps B1.1).
- No decorative data. Every lattice cell, count and date comes from the API response on
  screen. The landing page uses one labelled synthetic example and no invented metrics,
  customers or testimonials.
- No oversized empty heroes. The landing hero *is* a product moment: a real brief, rendered.
- Colour never carries meaning alone (keeps B15), and hatching never stands in for something
  the viewer may not know exists (§6.4).

---

## 3. Personality and voice

Four words: **exact, calm, candid, legible.**

- *Exact*: tabular figures, absolute dates, verbatim quotes, version and character offsets
  where they help.
- *Calm*: one signal colour at a time, and disagreement shown without alarm.
- *Candid*: the product says what it couldn't do, and why, in the first sentence.
- *Legible*: reading comes first. The answer is set like a document, not a widget.

Copy keeps today's rules: first person is never used for Lattice in UI chrome ("Lattice
can't confirm…"); the one exception, "I couldn't find evidence…", comes from the API and is
kept verbatim. Buttons are verbs. Policy notices always say the person *is* authorized when
they are.

---

## 4. Foundations

### 4.1 Typography — three voices with three jobs

| Voice | Family | Job | Why |
| ----- | --------------- | --- | --- |
| **Record** (serif) | **Newsreader** (Production Type, OFL, variable, optical sizes 6–72) | Answer prose, quoted passages, display headlines on landing, sign-in and Ask | Answers are documents people quote to customers. A text serif with optical sizing reads as *the record*, and separates generated-and-verified text from interface chrome at a glance. No mainstream AI or SaaS app sets its answers in a serif. |
| **Interface** (sans) | **Schibsted Grotesk** (OFL, variable) | Navigation, controls, tables, forms, admin copy | A newspaper-born grotesque with more character than Inter or Geist (open apertures, distinct `a`/`g`), dense enough for tables, and it pairs with Newsreader's news-text heritage |
| **Ledger** (mono) | **IBM Plex Mono** (OFL) | IDs, refs, dates, durations, codes, exhibit numbers | Clear `0/O`, `1/l/I` at 11–12 px; slab serifs give machine facts their own texture beside the grotesque |

Rules:
- **Mono is sentence case by default.** Uppercase tracked mono is kept for exactly two uses:
  the status keyword of a brief (`ANSWERED`, `PARTIAL`…) and the exhibit tag. Section headings
  become sans 13/600 with a count in mono. This one change removes most of the "handout" feel.
- The serif never appears in controls, tables or labels. The sans never sets answer prose.
- Fonts are self-hosted with `next/font/local` from `@fontsource-variable/*` files: the CSP
  allows `font-src 'self'` only. Subset to Latin + Latin-1 + punctuation; target ≤ 160 KB for
  all three families combined (variable files). Fallbacks: `Iowan Old Style, Georgia, serif`
  / `system-ui` / `ui-monospace`.
- **Gate:** a specimen route renders all three families at every size in the scale, with the
  `I l 1 | O 0`, tabular-digit and 200 % zoom checks, before D2 starts. If Schibsted Grotesk
  fails density in the 40 px table rows, the fallback is **Public Sans**; if Newsreader's
  small sizes fail on Windows rendering, the fallback is **Source Serif 4**.

Scale (px, line-height, weight). Role names stay close to today's so the migration is
mechanical:

| Role | Voice | Size / line | Weight | Use |
| ---- | ----- | ----------- | ------ | --- |
| `display` | Record | 44 / 48 (landing 56 / 60) | 500, opsz 60 | Landing hero, Ask desk heading, sign-in |
| `title` | Interface | 26 / 32 | 650 | Page titles |
| `lead` | Record | 21 / 32 | 450 | Direct-answer claims (lead paragraph of the brief) |
| `prose` | Record | 17 / 28 | 400 | Supporting claims, passages in the inspector (max 66ch) |
| `heading` | Interface | 15 / 22 | 600 | Section, dialog, evidence titles |
| `body` | Interface | 14 / 20 | 400 | Default UI |
| `small` | Interface | 13 / 18 | 400 | Excerpts, secondary rows, help |
| `section` | Interface | 13 / 18 | 600 | Section headings (replaces mono uppercase labels) |
| `meta` | Ledger | 12 / 16 | 400 | Dates, IDs, refs, counts, codes |
| `mark` | Ledger | 11 / 14 | 500, uppercase +0.06em | Status keyword, exhibit tag — only |

### 4.2 Colour — paper, ink, and five signals

The ground changes from yellow cream (`#FFFCF4`) to a **neutral archival paper**. It is
quieter and cooler, and separates Lattice from the warm-cream look common to AI products.
Ink is a blue-black. One brand hue, **verdigris** — the oxidised-copper green-blue of archive
fittings and survey instruments — does one job: *provenance* (citations, links to evidence,
trails, focus). It is not success green and not SaaS blue.

**Neutrals**

| Token | Value | Use |
| ----- | ----- | --- |
| `paper` | `#F4F3EF` | App and page ground |
| `sheet` | `#FCFCFA` | Reading surfaces: the brief, inspector, dialogs, inputs |
| `well` | `#EBEAE4` | Hover, sunken blocks, table header |
| `rule` | `#D6D4CC` | Hairlines and row dividers (decorative only) |
| `rule-strong` | `#878479` | Control boundaries (3.4 / 3.7 / 3.1:1 on paper, sheet, well) |
| `ink` | `#16181D` | Text, keylines, primary buttons (16.0:1 on paper) |
| `ink-2` | `#474A52` | Secondary text |
| `ink-3` | `#63666D` | Metadata, placeholders (5.2 / 5.6 / 4.8:1 on paper, sheet, well; 4.9:1 on the ochre tint) |

**Signals — one job each**

| Signal | Text / mark | Tint | Job |
| ------ | ----------- | ---- | --- |
| **Provenance** (verdigris) | `#0D6560` (6.2:1 paper, 5.7:1 tint) | `#DCEDEA` | Citations, exhibit links, trails, focus ring, the lit lattice cell. *"This can be followed."* |
| **Passage** (marker) | ink on `#F6DE79` | `#FBF0C2` | The verified quote in an excerpt or inspector, and the selected citation. *"This exact text."* Always behind ink, never a boundary. |
| **Disagreement** (plum) | `#76336E` | `#F1E4EE` | Potential conflict and supersession marks. Used on a rule and marker, not a filled box. |
| **Time** (ochre) | `#7D5300` | `#F6EBD2` | Stale evidence, old syncs |
| **Fault** (vermilion) | `#B0301C` | `#F8E2DC` | Errors, failed syncs, invalid input |
| **Healthy** (moss) | `#3C6223` | `#E5EDD9` | Sync succeeded. Always with a check icon, and visually distinct from verdigris in hue *and* by icon |

**Boundary** is not a colour. It is a texture: 45° hatching in `ink` at 14 % opacity on a 6 px
pitch, drawn as a CSS `repeating-linear-gradient` (no image, CSP-safe), plus a `rule-strong`
dashed edge. With `forced-colors: active`, the hatch is dropped and the dashed edge is kept.

Sensitivity stays monochrome, as today (Internal: no badge; Confidential: outlined + lock;
Restricted: ink-filled + lock). Sensitivity is stable and serious, and should not compete
with the signals.

Budget: at most one tinted region above the fold on Ask. Verdigris and marker can coexist
(that's the thread). Plum, ochre and vermilion are never tinted at the same time: a lower
priority signal steps down to a line with an icon.

**Dark theme:** the token architecture (§4.6) is built with a dark mapping from day one,
because three-layer tokens make it nearly free. It is not shipped: there is no user-facing
theme preference in this overhaul (§10, decision 3).

### 4.3 Space, grid and layout

- 4 px base, unchanged token names. New: **`Stack`, `Cluster`, `Inline`, `Page`, `Split`**
  primitives (they replace the per-module margins and the seven separate stacked-row rules)
  and a Stylelint rule that forbids raw px in component modules outside an allow-list
  (borders, icon sizes).
- **The margin column.** Reading surfaces get a 3-column grammar:
  `[margin 56][measure ≤ 66ch][notes 220]`. Exhibit numbers sit in the *margin*; source
  sidenotes sit in the *notes* column at ≥ 1440 px when the inspector is closed. Below that,
  notes fold inline. This grammar is shared by the brief, the inspector, the evidence page,
  the trace and the landing hero, so they align like pages of one book.
- **Admin pages** use a 12-column grid, max 1200 px, with the same left margin column so
  page titles, table first columns and the brief's prose share an x-axis.
- Gutters 32 / 24 / 16 at ≥1280 / ≥768 / <768 (unchanged).

### 4.4 Shape, rule and elevation

- Radii are smaller and more deliberate: 2 px (marks, highlights), 4 px (controls, tags),
  8 px (popovers, dialogs, sheets). Pills disappear: tags become squared 4 px labels, so
  exhibit markers and tags share one geometry.
- Rules: 1 px hairlines (`rule`) structure everything; a 1 px `ink` rule marks only *headers
  of authority* (the brief's status line, a table header, a dialog title). Today's ink keyline
  on every container goes away.
- One elevation, for floating layers only: `0 1px 0 rule, 0 12px 28px rgb(22 24 29 / 10%)`.

### 4.5 Iconography

- Lucide stays as the base set (1.5 px stroke at 16 px; ISC). It is consistent and complete,
  and replacing it gains nothing.
- **Six Lattice glyphs** are drawn on the same 16 px grid and stroke, to carry the product's
  own concepts: `exhibit` (numbered square), `passage` (lines with one marked), `boundary`
  (hatched square), `egress` (arrow crossing a hatched edge), `trail` (three linked nodes) and
  `lattice` (3 × 3, the brand mark). The source types get distinct silhouettes: document =
  folded page, ticket = notched ticket, chat = thread.
- Icons are never alone (unchanged B9).

### 4.6 Token architecture

Three layers in `tokens.css`, enforced by Stylelint:
1. **Palette**: raw ramps (`--verdigris-700`), used only inside `tokens.css`.
2. **Semantic**: roles (`--signal-provenance-text`, `--surface-sheet`, `--boundary-hatch`).
   Components may use only these.
3. **Component**: the few values a primitive owns (`--citation-size`, `--row-height-dense`).

A light theme and a dark theme each map palette → semantic. A contrast script (Vitest) checks
every declared text/background pair at ≥ 4.5:1, and every control boundary at ≥ 3:1, in both
themes.

### 4.7 Motion — motion explains a relationship or it doesn't happen

| Moment | Motion | Duration |
| ------ | ------ | -------- |
| Citation selected | Marker fill sweeps left→right under the claim; the matching lattice cell and exhibit row light together | 160 ms |
| Inspector opens | Slides 12 px + fade; the passage highlight sweeps in after the panel settles | 200 ms + 160 ms |
| Brief arrives | Status line, then lead, then exhibits fade-rise 4 px, 40 ms apart | 180 ms each |
| Boundary crossed (egress notice, withheld exhibit) | None. Boundaries are static by principle. | — |
| Hover / press | Background and colour only | 100 ms |

`prefers-reduced-motion`: no translation and no sweeps; state changes are instant. Nothing
loops. The progress line's elapsed counter ticks once per second, which is not an animation.

---

## 5. Signature patterns (the parts that make it Lattice)

1. **Exhibit marker.** A squared 18 px mono numeral, `sheet` fill with a `rule-strong` edge
   at rest, verdigris edge on hover, marker fill when selected. It is the same object in the
   brief, the margin, the exhibit list, the inspector header, the trace and the landing hero.
   It replaces today's lavender chip and lime circle.
2. **Provenance trail.** A mono, chevron-separated chain:
   `Halden Drive › DR-112 · Identity roadmap decision › v1 › chars 622–888 › quote verified ✓`.
   It sits at the top of the inspector and the evidence page, and in the trace. Each segment is
   a fact from the API; nothing is inferred (D-8 rules apply to the account segment).
3. **Provenance lattice** (Ask). A compact grid under the status line: rows = claims (short
   labels "Lead", "1", "2"…), columns = exhibits by number. A filled cell means the claim cites
   the exhibit; a plum-ringed cell means the exhibit is part of a potential conflict. Hover or
   focus a cell, and the claim and exhibit highlight together; activate it to open the inspector
   at that passage. It is an HTML `<table>` with row and column headers, so screen readers get
   "Claim 2 cites exhibit 3". It appears only when the relationship isn't one-to-one: some
   claim cites more than one exhibit, or some exhibit backs more than one claim. A diagonal
   (each claim one exhibit) says nothing the margin markers don't, so it would be decoration. On phones it collapses to a one-line "3 claims · 4 exhibits · 1
   conflict" that expands.
4. **Boundary hatching.** Used for: evidence the AI data policy kept from the model (on the
   row's leading edge, and in the "Not sent to the model" group header); the "not available"
   state; read-only regions in admin; the "private channels you aren't in" acknowledgement; and
   the edge of the egress lattice. **Never** as a placeholder for an item the viewer can't
   read (§6.4).
5. **Disagreement axis** (replaces the pink conflict box). Positions are laid out on a
   horizontal date axis, oldest left, newest right, with equal weight. Each position is a
   column: date, source kind and authority, the claim with its exhibit marker. A plum fork
   glyph sits at the top-left, and the conclusion sentence sits beneath it, unchanged ("Lattice
   can't confirm which is current"). The axis *shows* chronology without crowning it. Below
   768 px the axis turns vertical.
6. **The status keyword.** `ANSWERED · 3 CLAIMS · 4 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S` stays
   as the brief's ledger line, the one place where tracked uppercase mono remains. Exclusivity
   is what makes it recognisable.

---

## 6. Surfaces

### 6.1 Public landing page (`/`, new)

Purpose: say what Lattice is, why it can be trusted, and get the right people to sign in. It
is static: no API calls, no session probe, no organization names other than the labelled
synthetic example.

1. **Masthead** (56 px): mark + wordmark, three anchors (How it works · Boundaries · For
   administrators), and a *Sign in* button.
2. **Hero, split 5/7.** Left: serif display headline — "Answers you can follow back to the
   page." — a two-sentence deck, *Sign in* (primary) and *How it works* (text link). Right: a
   real rendered brief from the Halden SCIM scenario (lead claim, two supporting claims,
   exhibit markers, the disagreement axis and the provenance lattice). It is captioned in mono
   "Example · synthetic company Halden", not a screenshot, and is built from the same
   components and fixtures as the app, so it can't drift.
3. **The problem, in four rows** (fragmented, stale, contradictory, unprovenanced, from
   product-brief §4), each a one-line ledger row with a tiny glyph. It is not a card grid.
4. **How an answer is made**: one horizontal trail with five stops (*Your access* → *Search
   inside it* → *Policy check* → *Draft* → *Verify every quote*), with the boundary hatch drawn
   at the policy stop. Each stop has one sentence. This is the trace view's own geometry.
5. **Boundaries**: the egress lattice (levels × "approved external model / your
   infrastructure / stays in Lattice"), with the sentence "The model never receives what you
   can't read — and by default, nothing above Internal leaves Lattice." Plus an
   administrators-are-not-readers line (SEC-15).
6. **For administrators**: three rows covering access explained in sentences, every change
   previewed and audited, and source health without content.
7. **Footer**: sign-in, the security-model summary, © line. No logos, testimonials, metrics or
   "trusted by".

Indexing: `robots` stays `noindex` until there is an intentional public deployment (§10, decision 1).

### 6.2 Sign-in (`/sign-in`)

- One centred **credential sheet** (480 px) on paper, with a faint full-bleed lattice grid
  behind it: 1 px `rule` lines on a 48 px pitch, fading at the edges. This is the only
  decorative use of the grid, and it ties sign-in to the landing page and the mark.
- **Production:** wordmark → serif headline "Sign in to Lattice" → the SSO explanation (kept
  verbatim) → a quiet "What is Lattice?" link to `/`. The errors stay a closed set in one
  `role="alert"` row with the vermilion edge.
- **Development:** a hatched "Development sign-in" band at the top of the sheet (a boundary:
  this path cannot run in production), then personas grouped by organization as dense rows
  (name, role, e-mail in mono). The email form becomes a disclosure ("Use another seeded
  e-mail").
- After a redirect with `?next=`, the sheet says where you were going ("Continue to Access →
  Maya Okafor") — the path only, which is the viewer's own.

### 6.3 Application shell and navigation

**Top masthead, not a sidebar.** Lattice has five destinations. A sidebar spends 240 px of
every screen on them, and that width belongs to the brief and the inspector.

```
┌ masthead 52 ─────────────────────────────────────────────────────────────────────────┐
│ ▦ Lattice  Halden │ Ask  Activity  Sources │ Access  AI providers │   ⌘K Jump…   MO ▾ │
└──────────────────────────────────────────────────────────────────────────────────────┘
  [margin]  page title / brief                                   │ inspector (docked)
```

- Groups are separated by a hairline. The admin group renders only from `/v1/me`
  (unchanged). Active item: ink text with a 2 px verdigris underline on the masthead rule.
- **Jump (⌘K / Ctrl K):** a command palette for *Ask a question…* (prefills the composer),
  destinations, **your recent questions** (from `/v1/activity`, your own only), and for
  administrators, members (from `/v1/admin/access/members`, already admin-visible) and sources.
  It searches only lists the viewer's own endpoints return. There is no document or title
  search (security-model §10 "Autocomplete: none in the slice").
- **User menu** (initials): name, role label, organization, sign out.
- **"This session" goes away.** Recent questions come from `/v1/activity` (persistent, own
  only) and show on the Ask desk and in Jump. The in-memory history store is removed.
- **Page anatomy:** title (sans 26) + one-line deck + right-aligned actions, with no
  breadcrumb bar on top-level pages. Deep pages (member, trace, evidence) get an inline back
  crumb above the title.
- **Widths:** ≥1024: full masthead. 768–1023: destinations collapse into a "Menu" sheet; Jump
  and the user menu stay. <768: 52 px top bar (mark, page name, Jump) plus a **bottom tab bar**
  (Ask · Activity · Sources · More), 56 px with 44 px targets. Admin destinations sit under
  More.

### 6.4 Rules the new visuals must obey (security)

These are design rules with the force of SEC requirements. Each becomes a test in D7.

1. **Hatching never stands in for an unreadable item.** It is drawn only around things the
   viewer is entitled to know exist: their own readable evidence that policy withheld from the
   model, a page that is "not available" (identical for nonexistent, SEC-5), and their own
   read-only settings. No hatched "hidden row", count or slot ever appears in a list (SEC-14).
2. **Every lattice is built only from the response on screen.** The provenance lattice comes
   from `answer.claims` × `answer.evidence`; the egress lattice from `/v1/admin/providers`; the
   access lattice from the member endpoint, with the private-channel acknowledgement kept as
   one hatched cell *without* a name, count or reference (D-18).
3. **The landing page makes no API calls and names no organization** except the synthetic
   example. It does not reveal which organizations use Lattice (Phase 10 sign-in rule).
4. **Jump only lists what the viewer's existing endpoints return.** It adds no enumeration.
5. **Provenance trails omit absent segments.** The account segment follows D-8.

### 6.5 Ask

**Desk** (before a question):
- Serif display: "Ask what Halden knows." The deck is unchanged in substance.
- The composer, unchanged in behaviour (Enter / Shift+Enter, `/` to focus, limits), restyled
  as a `sheet` with a 1 px `rule-strong` edge that turns ink on focus. Scope controls become
  **scope tokens** inside the composer footer, read as a sentence: "Search **all my sources**
  · **any account** · **any date** · as **Internal**". Each bold phrase opens its popover
  (same Radix popovers and the same options as today, so the API-listed accounts and readable
  types are unchanged).
- **Your reach**, beneath the composer, replaces the three principles. It shows the sources you
  can search (name, type glyph, readable-document count, freshness from `last_succeeded_at`)
  and your accounts, from `/v1/sources` and `/v1/accounts`. It answers "what can I ask
  about?". Clearance is not shown because `/v1/me` doesn't return it, and the UI must not guess.
- **Recent questions** (5, from `/v1/activity`) with status keywords; each opens its trace or
  re-asks.

**Progress:** unchanged honesty (one line, elapsed seconds, no stages; D-6 open). The line
names the sources being searched (from "Your reach") and holds the brief's geometry in a
skeleton of rules, not grey bars.

**Brief** (answered / partial):
```
[margin] ANSWERED · 3 CLAIMS · 4 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S        Copy · Trace
         ─────────────────────────────────────────────────────────── (ink rule)
         ▧ 1 source you can read wasn't sent to the AI model (AI data policy)   ← hatch-edged line
         ┌ provenance lattice ┐
 [1]     A decision record dated 18 Sep moves SCIM GA to December, with a      │ DR-112
         November beta for Acme's pilot group.  (lead, serif 21)                │ 18 Sep 2026
 [2][3]  At the June kickoff Halden told Acme it was targeting Q3. On 14 Aug,   │ Kickoff notes
         Support told Acme October.  (prose, serif 17)                          │ HAL-4821
         ── Disagreement · SCIM GA date ── axis: 14 AUG ──────── 18 SEP ──
         What the evidence doesn't establish — …
         Exhibits  3 cited · 1 not sent · 2 also retrieved
```
- The margin holds exhibit markers aligned to the first line of each claim. Inline markers
  remain after the sentence for reading flow and accessibility (each is a `button` with the
  same accessible name as today).
- Notices keep their order and wording. The stale notice is the one ochre-tinted line; the
  egress notice gets the hatch edge.
- **Exhibits list:** rows on `sheet` with a margin marker, type glyph, title (sans 15/600),
  provenance meta (mono), and the excerpt (serif 15) with the quote in marker tint. The "Not
  sent to the model" group has a hatched leading edge; "Also retrieved" stays collapsed.
- **States:** each B13 state keeps its logic and copy, mapped to the four state families in
  §7.3. Insufficient and restricted remain identical (SEC-5).

**Inspector → Exhibit viewer:** header (marker, type glyph, ←/→, close), then the provenance
trail, the title (sans 18/650), and the passage set as a page: serif 17 on `sheet`, the
verified quote in marker tint, with a verdigris bar in the margin beside the cited range.
Below: "Cited by" (the claims, each with its marker), then metadata as a compact two-column
ledger. Keys stay `[`, `]`, `Esc`. The geometry is the same docked / drawer / sheet as today,
but docked from 1280 px with the brief keeping ≥ 600 px now that there is no sidebar.

**Evidence page (`/evidence/{id}`)** and **Trace (`/traces/{id}`)** reuse the exhibit viewer
and the brief components respectively. The trace's pipeline becomes the horizontal-trail
language from the landing page (vertical on phones): exhibit-style numbered stops, durations
in mono, failures in vermilion, skipped stops hatched. The administrator's trace view shows
only the stops, with one hatched "Question, answer and evidence aren't visible to
administrators" band (SEC-15).

### 6.6 Sources

- **Members:** a ledger of the sources they can read. Each row has the type glyph, name, "N
  documents you can read", and a **freshness rule**: a thin horizontal bar whose fill and label
  show age since `last_succeeded_at` (moss < 1 day, ink < 7 days, ochre after), with the
  absolute date in mono. This is a reading of one fact the API gives, not a chart of invented
  history.
- **Administrators:** the same ledger plus permission mode (sentence form: "Follows the
  source's sharing" / "By account"), default level, totals, last-run status, failed records,
  and *Sync now*. Failures expand inline under the row (rather than in a separate section
  below the table), with an opaque-reference explanation that the mono `rec_…` makes
  self-evident.
- One `DataTable` primitive (§7.2) serves all four data surfaces.

### 6.7 Activity

- **Your questions:** grouped by day (Today / Yesterday / date), each row a status keyword +
  question (sans 15) + time (mono); it opens the trace. A filter by status is client-side
  over the loaded page only.
- **Organization activity (admins):** the same table anatomy. The question column is
  *present but hatched*, labelled "Content not visible to administrators", which makes SEC-15
  visible instead of merely explained. Every row has it, so it reveals nothing about any row.

### 6.8 Access

- **Members:** the DataTable with name and e-mail, status (Active / Suspended / Identity
  provider tag), clearance as a 3-step **level meter** (three squares, filled to the level),
  accounts, departments. Search stays.
- **Member:** the access sentence stays as the page's lead (it is the best thing on the page),
  set in serif 21. Under it sits the **access lattice**. Rows: *Clearance*, *Accounts*,
  *Departments*, *Groups & channels*, *Direct grants*; cells: the values as tags. One hatched
  cell says "Also in private channels you aren't in" (no name, count or reference, D-18). The
  identity-provider notice, actions and *Check a document* follow.
- **Change access dialog:** edit → **review as a diff**. Two columns, *Now* and *After*, rule by
  rule, with changed rows marked by a verdigris (gain) or vermilion (loss) margin bar and the
  effect sentences beneath. Then the counts (still "Counted only among documents you can
  read"), the required reason, and *Apply change*. A sheet on phones.
- **Audit log:** a ledger timeline. A date rail in the margin; each event is actor → action →
  subject, with detail rows ("Name: Pat Doe → Pat Rivera") in mono. Keyset paging stays ("Load
  older").

### 6.9 AI providers

- The read-only statement becomes a hatched band at the top ("Changed only through the audited
  `lattice provider` and `lattice org egress` commands"), so read-only is visible, not just
  said.
- **Egress lattice:** rows Internal / Confidential / Restricted; columns each configured
  provider (grouped Generation / Embedding) and a final "Stays in Lattice" column. A filled
  cell means "may be sent", a hatched cell "kept from this provider". It is computed from the
  policy and each provider's approval, exactly as the table states today. A one-line legend
  sits under it.
- Below it, the provider DataTable (trust class, approval, status, credential state, data
  terms), with credential problems in ochre and an icon.

### 6.10 Empty, loading and error states — four families

| Family | Look | Used for |
| ------ | ---- | -------- |
| **Quiet** | Sans heading, one sentence, what was searched (mono ledger), suggestions | No evidence, empty lists, nothing indexed |
| **Boundary** | Hatched square glyph, heading, one sentence; identical for nonexistent and unreadable | Not available (evidence, trace, member, admin route) |
| **Fault** | Vermilion margin bar, heading, sentence, request reference (mono), *Try again* | Network, provider failure, unexpected |
| **Wait** | Ochre margin bar, heading, the wait in plain words, draft preserved | Rate limit |

Loading uses **rule skeletons**: the real layout's hairlines and margin markers are drawn
immediately, and text arrives into them. There are no grey bars and no shimmer. The shell
never shows a spinner.

### 6.11 Forms and filters

One field anatomy: label (sans 13/600) above, control on `sheet` with a `rule-strong` edge,
help (sans 12) below, and an error with an icon plus text in vermilion below the help. Primitives
for Input, Textarea, Select, Search, Checkbox, Radio and Date. Filters in admin tables are a
**filter line** above the table that reads as a sentence, like the scope tokens.

---

## 7. Design-system primitives (target inventory)

### 7.1 Foundations
`tokens.css` (palette / semantic / component, light + dark maps) · type roles · `Stack`,
`Cluster`, `Inline`, `Split`, `Page`, `MarginGrid` · `VisuallyHidden` · contrast test ·
Stylelint rules (no raw colour, no raw spacing, no `t-label` outside `mark`).

### 7.2 Components (new ★, rebuilt ◆, kept ○)

| Component | | Notes |
| --------- | - | ----- |
| Button, ButtonLink, ExternalLink | ◆ | Primary (ink), secondary (`rule-strong` edge), quiet, danger; sizes sm/md/lg; loading keeps width |
| Field, Input, Textarea, Select, Search, Checkbox, Radio, DateInput | ★ | One anatomy (§6.11); replaces 3 ad-hoc inputs |
| ScopeTokens / FilterLine | ★ | Sentence-style filters over Radix Popover |
| Tag, LevelMeter, StatusMark | ◆★★ | Squared tags; 3-step level meter; status keyword + icon |
| ExhibitMarker | ★ | §5.1 — replaces citation chip and stage marker |
| ProvenanceTrail | ★ | §5.2 |
| Lattice (matrix) | ★ | Accessible `<table>` grid; cell states: filled, hatched, conflict, empty |
| Hatch | ★ | Texture + dashed edge; `forced-colors` fallback |
| DisagreementAxis | ★ | §5.5 |
| Notice (line) / Callout | ◆ | One-line notices; tinted callout only for the single top-priority state |
| StateBlock (Quiet/Boundary/Fault/Wait) | ★ | §6.10 — replaces `ErrorState`, `NotAvailable`, `States.module.css` |
| DataTable | ★ | Header, sortable where the API sorts, dense mode, stacked rows < 1024, sticky first column, row actions, inline expansion — replaces 4 tables |
| Tabs, Menu, Popover, Tooltip | ◆ | Radix |
| Dialog, Sheet, Drawer | ◆ | Same focus rules as today (return focus to opener) |
| CommandPalette (Jump) | ★ | Radix Dialog + listbox |
| Toast | ★ | "Link copied", "Change applied"; `role="status"`, 4 s, never the only confirmation of a write |
| Masthead, BottomBar, UserMenu, PageHeader | ★◆ | §6.3 |
| Skeleton (rule skeleton) | ◆ | §6.10 |
| Wordmark / Mark | ◆ | 3 × 3 lattice with one lit verdigris cell; 16, 20, 32 px; favicon |
| Kbd | ★ | Keyboard hints |

### 7.3 Accessibility contract (WCAG 2.2 AA, unchanged target, stricter coverage)

- All text pairs ≥ 4.5:1 and control boundaries ≥ 3:1, tested in both themes (§4.6).
- Lattices are tables with `<th scope>`, and every cell has an accessible name ("Claim 2
  cites exhibit 3"). Hover linking has a keyboard equivalent (focus).
- Hatching is never the only signal: it always comes with text or an accessible label, and
  has a `forced-colors` fallback.
- Focus: 2 px verdigris outline, 2 px offset, ≥ 3:1 against paper and sheet.
- Targets ≥ 24 px (44 on touch layouts). Bottom bar 44 px.
- Keyboard map documented on a `?` overlay: `/` composer, `⌘K` Jump, `[ ]` exhibits, `Esc`
  close, `g a / g s / g y` go to Ask / Sources / Activity.
- Reduced motion, 200 % zoom and text-spacing overrides are checked per screen in D7.

---

## 8. Implementation plan

Frontend only. Every step keeps the API contract, the security behaviours and the existing
test suites green, and updates selectors and copy in the same commit. Each step ends with
`pnpm lint && pnpm typecheck && pnpm test && pnpm build` and the Playwright desktop and mobile
projects. Steps that change visuals regenerate the screen harness.

| Step | Scope | Exit criteria |
| ---- | ----- | ------------- |
| **D0 — Direction sign-off** | This document + the visual direction board; answers to §10 | Product owner accepts or amends; open questions closed |
| **D1 — Foundations** | Fonts self-hosted (`next/font/local`), three-layer tokens with light + dark maps, type roles, layout primitives, Stylelint rules, contrast test, the six glyphs and the new mark; a dev-only `/_specimen` route rendering every token, role and glyph | Type gate (§4.1) passed at 5 widths, on macOS and Windows-like rendering (Playwright Chromium + font-smoothing off); contrast test green in both themes; bundle font weight ≤ 160 KB |
| **D2 — Primitives** | Everything in §7.2 except surfaces; component tests + axe per primitive; specimen route shows all states | Each primitive has states, keyboard and axe tests; no surface changed yet |
| **D3 — Shell & navigation** | Masthead, user menu, Jump, bottom bar, page anatomy; remove the sidebar, rail and in-tab history; recents from `/v1/activity` | Shell, keyboard and mobile Playwright suites updated and green; geometry invariants rewritten for the new axes; SEC check: Jump lists only endpoint results |
| **D4 — Ask, evidence, trace** | Desk, scope tokens, your reach, recents, progress, brief, margin markers, provenance lattice, disagreement axis, exhibits, exhibit viewer, evidence page, trace; all B13 states | All 13 state fixtures render; citation-to-evidence and hidden-trace safety tests unchanged and green; the lattice's a11y names tested; no exhibit marker for absent evidence (existing test kept) |
| **D5 — Admin & operations** | DataTable migration for Sources, Activity, Access members, Providers; access lattice; diff review dialog; audit ledger; egress lattice | Existing access, sources, activity and provider e2e green; SEC-14/D-18 tests extended to the access lattice and hatched cells |
| **D6 — Public & identity** | Landing page (static, shared components, synthetic example), sign-in (prod, dev, errors, `next`), not-found, global error | Landing makes zero `/api` requests (asserted); no organization name other than the labelled example; identity e2e green; CSP unchanged |
| **D7 — Verification & docs** | Screen harness at 1440/1280/1024/768/390 in light (the shipped theme; the dark map is checked by the contrast test and the specimen, §10 decision 3); axe for every screen and state; reduced motion, 200 % zoom and forced-colors passes; §6.4 rules as tests; rewrite `design-system.md` v2; README screenshots | All suites green twice in a row; design-system v2 merged; this document marked Implemented |

Sequencing notes:
- D3 and D4 are the riskiest (layout axes and the most tests). The plan lands them behind no
  flag: each step is a coherent whole and merges only when green.
- Machine limits (M1, 8 GB): one heavy task at a time. Builds and Playwright run serially,
  with no local model running alongside.
- Expected size: about 15–25 focused commits. No backend or OpenAPI change. If a design
  needs data the API lacks (e.g. the member's own clearance on the Ask desk), it is listed as
  a contract observation and the design does without it.

---

## 9. Risks and how they are contained

| Risk | Containment |
| ---- | ----------- |
| Serif answers read as "old-fashioned" | Optical sizing and a modern grotesque around it; validated on the specimen and on real fixtures in D1 before any surface work |
| The provenance lattice confuses rather than helps | It shows only with ≥ 2 claims and ≥ 2 exhibits, is labelled, and is fully keyboard/AT accessible. If D4 review finds it noisy, it collapses by default to the one-line summary. |
| Hatching is read as "something hidden from you" | Each hatched region is labelled in words, and the rules in §6.4 forbid hatching for anything the viewer may not know exists |
| Removing the sidebar hurts discoverability for admins | Labelled masthead groups, Jump, and admin entries under More on phones; Playwright checks that every destination is reachable by keyboard at every width |
| Test churn | Accessible names of controls and states are kept stable where the copy doesn't change; selector changes land in the same commit as the component |
| Font licensing or availability | All three families are SIL OFL; fallbacks are named in §4.1 |

---

## 10. Decisions (D0, accepted by the product owner 2026-10-02)

| # | Decision |
| - | -------- |
| 1 | **Landing page** lives at `/` inside `apps/web`. It stays `noindex` until there is an intentional public deployment. |
| 2 | **Typography**: the three-voice system — Newsreader (record: answers, passages, display), Schibsted Grotesk (interface), IBM Plex Mono (ledger: factual metadata) — subject to the D1 implementation and type-quality gate. |
| 3 | **Dark theme**: the dark token map is built in D1 so the system is structurally ready. No user-facing dark-mode preference ships in this overhaul; light is the shipped experience. |
| 4 | **Navigation**: the masthead + Jump (⌘K) direction replaces the desktop sidebar, with the proposed phone navigation. The shell is designed so future product sections can be added without another shell redesign. |
| 5 | **Brand signal**: verdigris is the primary provenance/brand signal. Lime, lavender and pink are retired. The other colours stay strictly semantic, never decorative. |

Implementation runs D1 → D7 in order, each reviewed before the next starts.

---

## 11. Implementation log

### D1 — Foundations (2026-10-02, `phase/design-overhaul`)

**Built.** Self-hosted fonts (`apps/web/scripts/build-fonts.py` → `public/fonts/`,
`src/styles/fonts.css`, `src/lib/fonts.gen.ts`); the three-layer `tokens.css` (palette
`--p-*` → semantic → component) with the light map shipped and the dark map under
`[data-theme="dark"]` only; type roles in `globals.css`; layout primitives
(`components/layout`: Stack, Cluster, Inline, Split, Page, MarginGrid); the mark, wordmark
and favicon (`components/brand`, `app/icon.svg`); Lattice glyphs (`primitives/glyphs.tsx`,
also used by the source-type tag); the development-only `/specimen` route. Every component
moved off the v1 tokens and type classes; Geist was removed. Surfaces keep their v1 layouts
on the v2 foundation until D2–D6 rebuild them.

**Type gate (§4.1): passed.** Latin total 161,312 bytes (157.5 KiB), inside the 160 KiB
budget, by limiting variable axes to the ranges the scale uses (Newsreader wght 400–600 ·
opsz 12–72, 132 → 89 KB; Schibsted Grotesk wght 400–700). `0/O`, `1/l/I` are distinct in the
ledger voice at 12 px. In the interface voice, `I` and `l` differ only by height, as in most
grotesques; identifiers are always set in the ledger voice, which carries that burden. Both
fallbacks named in §4.1 were not needed.

| Planned | As built | Why |
| ------- | -------- | --- |
| `next/font/local` | A generated `@font-face` file: one family per voice, Latin and Latin Extended faces split by `unicode-range`, metric-matched fallback faces (Georgia / Arial / Courier New with `size-adjust` and ascent/descent overrides), content-hashed files with an immutable cache policy, the three Latin files preloaded | `next/font/local` applies one set of declarations to every file of a call and puts its fallback face ahead of any second family, so Latin Extended could not be loaded on demand without being shadowed |
| Type roles of §4.1 | Adds `subtitle` (18/24, inspector and dialog titles), `body-strong`, `caption` (12/16 help text) and `code` (13/20) | Roles the existing surfaces need; no other sizes exist |
| Palette of §4.2 | Adds `--surface-well-strong` (`#E6E4DD`, pressed/selected) and a `-mark` token per signal for rules and markers (≥ 3:1); the ochre mark is `#A57A22` | The contrast test failed the first values (`#E3E1DA` gave muted text 4.39:1, the ochre mark 2.82:1) |
| v1 "info" status colour | None: neutral notices use secondary text on `--surface-well` | §4.2 has five signals; info was a sixth hue with no single job |
| Mark: 1.5 px lines throughout | Frame 1.5, inner lines 1 | At 16 px the thicker inner lines swallowed the lit cell |
| Raw-px Stylelint rule everywhere | Enforced for the rebuilt directories (`brand`, `layout`, `specimen`); widened as D2–D6 rebuild each surface | 162 raw px values live in v1 surface modules that are rewritten later |
| Shell tokens | `--header-height`, `--sidebar-width`, `--rail-width` and friends kept, marked as v1 shell geometry | Still used by the sidebar shell until D3 replaces it |

**Verification.** `tokens.test.ts`: 61 colour pairings × 2 theme maps (4.5:1 text, 3:1
boundaries), identical semantic keys in both maps, every `var()` resolving, no palette token
in a component, no v1 name anywhere. `e2e/foundations.spec.ts`: fonts only from `/fonts`
(no third-party request), preloaded, immutable; Latin Extended requested only by a page that
uses it; the page holds with every web font blocked; the type scale measured at 1440 and
390 px; no overflow at the five review widths; axe and the live contrast ledger in both
maps; the product ships the light map with no theme attribute.

### D2 — Shared primitives (2026-10-02, `phase/design-overhaul`)

**Built** (`apps/web/src/components/primitives`), each shown in its states on `/specimen`
§09–§18:
- **Field family:** Field, TextInput, TextArea, Select (native), SearchInput (clear button,
  Escape clears), DateInput, Checkbox, Radio, ChoiceGroup.
- **Marks:** Tag, LevelMeter, StatusMark, Kbd.
- **Provenance:** ExhibitMarker and ExhibitButton, ProvenanceTrail, Lattice and
  LatticeLegend with the `latticeAddsInformation` gate.
- **Boundaries and states:** BoundaryBand, BoundaryEdge, BoundaryInline, DisagreementAxis,
  StateBlock (quiet, boundary, fault, wait).
- **Interaction:** FilterLine and FilterToken, Popover, Menu, Toast (provider mounted), Jump
  with `useJumpShortcut`, DataTable, and `useOverflow` (a table or lattice that scrolls
  becomes a focusable, named region).
- **Rebuilt in place:** Button (ink primary, `danger`, a pressed state, `ghost` → `quiet`),
  Callout and NoticeLine (margin bars; tones are now the signals: `neutral`, `time`,
  `fault`, `disagreement`, `boundary`), Modal, Drawer and Tooltip.

**v1 forks removed now:**
- The trace stage marker, the citation chip, and the evidence-row and inspector numbers →
  ExhibitMarker. Accessible names, `aria-pressed` and `data-citation` are unchanged.
- `ErrorState` and `NotAvailable` render through StateBlock; their stylesheet is deleted.
- The AI-policy notice uses the `boundary` tone; supersession uses `disagreement`.

**Still to migrate, by step:** the sidebar shell (D3); Ask's scope chips → FilterLine (D4);
the four page tables → DataTable, and the Access and sign-in inputs → Field (D5 / D6).

| Planned | As built | Why |
| ------- | -------- | --- |
| Inline exhibit markers with a 44 px hit area on touch | Real 24 px markers on coarse pointers; neighbouring markers 6 px apart (24 px between centres) | Overlapping invisible hit areas let neighbours steal taps, and 18 px markers 2 px apart fail WCAG 2.5.8 |
| DataTable "sortable where the API sorts" | No sorting | No list endpoint sorts; reordering one loaded page would mislead |
| Proportional disagreement axis | Positions evenly spaced, interval written ("35 days later", "Same day") | A distance scale turns dates into weight; equal weight is the rule |
| Menus modal (Radix default) | Non-modal | A modal menu hides the page from assistive tech while it stays focusable (axe `aria-hidden-focus`); a menu isn't a dialog |
| One table style for every lattice | `headers="interface"` for word axes (data levels, providers) | Ledger figures suit numbered axes only |

**Defects the new tests found and fixed:**
- Drawer didn't return focus to whatever opened it (only to a Radix trigger), which
  affected the phone navigation sheet.
- Jump group ids contained spaces, which left its groups nameless.
- Option and axis-heading names ran together ("…Acme?Answered", "Potential
  conflictSCIM").
- Lattice arrow keys skipped rows.
- The egress lattice overflowed (and so was an unfocusable scroll region) because of a
  negative margin.
- An axe audit raced the inspector's fade-in. Audits now wait for animations to settle.

**Verification.**
- `primitives.test.tsx`: 26 tests (wiring, names, keyboard, states).
- `e2e/primitives.spec.ts`: 7 browser flows, axe with overlays open:
  - Jump by ⌘K and focus return;
  - lattice roving focus that lights the related claim;
  - filter tokens;
  - the menu, dialog and sheet focus trap and return;
  - toast;
  - DataTable stacking at the five widths;
  - the phone axis and 24 px targets.
- Full gate: lint, typecheck, 115 unit tests, production build, 117 Playwright tests
  (desktop and phone).

### D3 — Application shell and navigation (2026-10-02, `phase/design-overhaul`)

**Built** (`apps/web/src/components/shell`):
- **One navigation model** (`nav.ts`): sections (*Workspace*; *Administration*, shown only
  with the `admin` capability from `/v1/me`) and destinations, each with the paths it owns
  (`/traces/*` → Activity, `/evidence/*` → Ask, `/access/*` → Access) and a phone priority
  (tab or More). The masthead, the phone bars, the More sheet and Jump all render from it,
  so a future destination is added once.
- **Masthead** (≥768 px, 52 px including its ink rule, sticky): mark + wordmark (home), the
  organization after a hairline, the sections as two lists separated by a hairline, Jump,
  and the account menu. The current page is ink, weight 600 and a 2 px verdigris border
  on the masthead rule (a border, so forced colours keep it), with `aria-current="page"`.
  Text only: no icons in the bar.
- **Measured fit instead of a breakpoint menu.** The masthead shows everything inline
  whenever it fits and gives space back in a fixed order, measured before paint: (1) Jump
  drops its word, keeping the icon and key hint; (2) later sections fold into one menu each,
  named for the section ("Admin"), whose trigger carries the active mark and "current page:
  …" in its name; (3) the organization name leaves the bar (it is always in the account
  menu); (4) everything folds into "Menu". It re-fits on width, font and content changes.
  With today's English labels every destination stays inline down to 768 px (only step 1
  happens); the steps exist for long organization names, translations, 200 % zoom at
  tablet widths and future destinations.
- **Account menu** (initials, squared like an exhibit marker): name, e-mail, role label,
  organization, *Sign out* — all from `/v1/me`; sign out is the only account action the
  product has.
- **Phone** (<768 px): a 52 px top bar (mark → Ask, the current destination's name over the
  organization, a page-action slot for Ask's *New question*, Jump) and a fixed bottom tab
  bar (Ask · Activity · Sources · More, at most four tabs; extra destinations move to More).
  Labels wrap to a second line rather than truncate; the page reserves the bar's measured
  height, so nothing is ever under it. Safe areas: `viewport-fit=cover`, insets on both bars
  and on `--gutter` (now `max(--gutter-base, safe-area insets)`), `100svh` minimums so
  browser chrome never moves the layout. **More** is a bottom sheet with the
  administration links (when capable) and the account with *Sign out*; when the current
  page lives there the More tab carries the mark and "More, current page: Access".
- **Jump wired** (`ShellJump.tsx`), only from endpoints the viewer already uses:
  destinations from the model (plus the audit log for administrators); **your recent
  questions** from `GET /v1/activity` (fetched only while Jump is open; five before typing,
  all loaded ones when searching; each opens its trace, which the API re-authorizes); and,
  for administrators only, **members** from `GET /v1/admin/access/members?q=` once two
  characters are typed (debounced) — the Access page's own search. *Ask “…”* puts the
  question in the composer, focused and unsent (`state.prefill`). Members never trigger an
  administrator request.
- **Shortcut:** ⌘K on Apple platforms, Ctrl K elsewhere; never the other platform's chord
  (Ctrl K on a Mac keeps its delete-to-end-of-line meaning in fields). Ignored during IME
  composition, on key repeat, and while a modal dialog (Modal, Drawer — marked
  `data-modal`) is open; menus and popovers don't block it. Toggles closed.
- **Focus after navigation:** the shell now owns `<main id="main" tabindex="-1">` (pages
  render inside it). After a route change, or a link to the page already open, focus moves
  to the page if it was left on the shell's navigation or nowhere; a page that placed focus
  itself (the composer after *Ask “…”*) keeps it. Jump and the More sheet don't restore
  focus to their opener after a choice; they do on dismissal.
- **Canvas:** the page spans the full width under the masthead; the inspector docks beside
  it under the masthead rule (≥1280 px). `scroll-padding` keeps focused content clear of the
  sticky masthead and the tab bar (WCAG 2.4.11). Pages keep their own measure (reading or
  admin width) until D4–D6.
- **Page anatomy, shell part only:** the 56 px page-header bar is gone. Deep pages (trace,
  evidence, member) show an inline back trail (`PageTrail`, `nav "Breadcrumb"`) above their
  content; top-level pages have none. Titles stay page-owned until D4–D6.
- **Skeleton:** the masthead rule (and on phones the tab-bar band) without destinations,
  which depend on capabilities not yet loaded.

**Removed:** `Sidebar`, `Rail`, `TopBar`, `PageHeader` (and their styles); the in-tab
"This session" history (`state.history`, `HistoryEntry`, `HISTORY_LIMIT`) — Activity and
Jump read the persisted `/v1/activity` instead; the Drawer's `left` side (only the
navigation sheet used it); tokens `--header-height`, `--topbar-height`, `--sidebar-width`,
`--rail-width`, `--nav-row`, `--z-rail`. New: `--masthead-height`, `--bottom-bar-height`,
`--panel-header-height` (the inspector's header row), `--z-shell`, `--gutter-base`. One
presentation renders at a time (chosen by width), not two hidden behind breakpoints.

**Primitive changes:** Jump gains `onQueryChange`, a `title` on truncated labels and the
platform chord (`jumpChord`); Menu's `MenuLink` gains `current` (weight + leading bar) and
`onClick`; Drawer gains `fit` (a bottom sheet as tall as its content) and loses `left`;
Modal and Drawer mark their content `data-modal`.

| Planned (§6.3) | As built | Why |
| -------------- | -------- | --- |
| 768–1023: destinations collapse into a Menu sheet | Measured fit (four steps above); all destinations inline at 768 with English labels | A fixed breakpoint either hides routes that fit or overflows with long names and translations; folding only what doesn't fit keeps the everyday pages one click away |
| Phone top bar: mark, page name, Jump | Adds the organization under the page name, and the existing page-action slot | The v1 phone bar showed the organization; context is kept |
| User menu: name, role label, organization, sign out | Adds the e-mail (ledger voice) | It is the identity the person signed in with |
| Jump: members **and sources** for administrators | Members only, after two typed characters; plus the audit log as a destination | There is no per-source page to go to (only `/sources`), and listing members before a search would be enumeration for its own sake |
| Recent questions on the Ask desk | Jump only | The desk is D4 |
| `?` keyboard overlay, `g a / g s / g y` | Not built | Not part of the D3 brief; D7 decides with the full keyboard map |

**Defects found by the new tests and fixed:**
- The More tab's and folded menu's names read "More , current page…" (a hidden span adds a
  space); they now use one `aria-label` that starts with the visible word.
- The docked inspector overlapped the masthead's rule by 1 px; `--masthead-height` now
  includes the rule everywhere.
- Ctrl K would have replaced a Mac text field's delete-to-end-of-line.
- The back trail echoed the requested id ("Evidence e0000000…", "Evidence" for a malformed
  one), so not-available pages differed inside `main`; the trail now says only "Evidence" /
  "Trace" (the existing SEC-5 identical-page tests caught it once the trail moved into
  `main`).
- The skip link could appear in full-page captures; it is now clipped until focused.

**Verification.**
- `shell.test.tsx` (9): the nav model by capability, deep-page ownership, the phone split
  and its growth rule, initials; the platform chord and every case the shortcut ignores;
  ShellJump for a member (no administrator request whatever is typed), an administrator
  (members only after typing, from the Access endpoint) and a failed activity load.
- `e2e/shell.spec.ts` (28 desktop, 10 phone), replacing the sidebar suite: landmarks and
  no v1 shell; `aria-current` on exactly one destination for seven paths, with weight and
  a 2 px rule; sections by capability; a long organization name at 1440 / 1024 / 768 and
  in the account menu; the fold to a named section menu; keyboard order and focus after
  navigation (including the page already open) and the skip link; the account menu and
  sign out; axe on Ask, Sources, Access and with Jump open; Jump by shortcut and control
  with focus return, the other platform's chord ignored, keyboard selection, *Ask “…”*,
  recent questions, no results, members' and administrators' results with the admin
  request log, the shortcut blocked under a modal, reduced motion; the docked inspector
  under the masthead, the reading column, no overflow at the five widths on five screens,
  and 200 % zoom (640 CSS px); on phones: bars, 44 px targets, no overlap at the end of the
  page, tab navigation and focus, More for administrators and members with focus return,
  a long organization name, 320 px reflow, Jump, the evidence sheet and *New question*.
- Visually reviewed (harness and targeted captures): Ask, Sources, Access and the member
  page in the shell at 1440, 1280, 1024, 768 and 390; Jump idle and with a member search;
  the account menu; the folded Admin menu at 768; the More sheet. Page content is still
  v1 layout on v2 foundations until D4–D6.
- Full gate: lint, typecheck, 124 unit tests, production build, 307 Playwright tests
  (desktop, phone and the screen harness) run with one worker.
- Harness note for D7: full-page captures of a scrolled page stitch the sticky masthead
  mid-image; D7's captures should scroll to the top or capture the viewport.

### D4 — Ask, evidence and trace (2026-10-03, `phase/design-overhaul`)

**Built.** The question → answer → evidence → trace experience, rebuilt on the D1–D3
system. Frontend only: no API, schema or authorization change.

- **Record grammar** (`components/record`): one page container and two edges shared by
  Ask, the trace and the evidence page — `[margin 56][measure 36rem][notes 220]`. Prose
  keeps the measure; the composer, ledger line and exhibit list run from the measure to
  the notes column's far edge; markers sit in the margin. `--record-gap` is a layout token.
- **Desk** (`AskDesk`): a one-line serif heading and deck (no hero), the composer, then
  **Your reach** (each readable source from `/v1/sources` — type glyph, "N documents you
  can read", last successful sync — and the accounts from `/v1/accounts`) beside **Your
  recent questions** (five, from `/v1/activity`; each opens its trace, or *Ask again* puts
  it back in the box, unsent and focused). The empty knowledge base is a quiet state and
  disables the composer.
- **Composer and scope** (`Composer`, `ScopeLine`): a sheet whose edge turns ink on focus;
  the box grows to eight lines; Enter asks, Shift+Enter adds a line, `/` focuses. The scope
  is one sentence of D2 filter tokens — *Search all my sources · any account · any date · as
  default sensitivity* — each opening the D2 field primitives (checkboxes, radios with help,
  date fields, a filter for ≥ 7 accounts). A narrowed token has its own clear button. An
  invalid date range is caught before sending, in words, on the token and under the box.
  The question survives every recoverable failure. *New question* (phone top bar) clears
  and focuses the box.
- **Progress** (`AskProgress`): the brief's own ledger line (`ASKING`, elapsed seconds),
  one sentence naming how many readable sources are searched, and a rule skeleton of the
  claims' geometry. No stages, no percentages, nothing loops.
- **Brief** (`AnswerView`, `Brief`): the ledger line (`ANSWERED · 3 CLAIMS · 3 EXHIBITS ·
  NEWEST … · 4.2 S`, *Copy* · *Trace*; answered has no icon or colour), API notices in API
  order (stale is the one tinted line, the AI-policy notice the hatched edge), then one row
  per claim — lead in the record voice at 21 px, supporting at 17 px — with its exhibit
  markers in the margin aligned to the first line, and at ≥1440 px with nothing docked a
  sidenote naming its exhibits. Then disagreement, what the evidence doesn't establish,
  the lattice when it adds information, and the exhibits. The brief arrives status line →
  claims → rest, 4 px and 40 ms apart (none under reduced motion). *Copy* puts the claims
  with their `[n]` numbers and the exhibit list on the clipboard.
- **Claim ↔ exhibit** (`answer-context` pointer store, `Markers`): hovering or focusing a
  marker points at one relationship (its claim, its exhibit row and its lattice cell light
  together); hovering a claim's words, an exhibit row or a lattice column lights everything
  linked to it. Opening an exhibit selects every marker of it and underlines the claims that
  cite it in the marker colour. The keyboard reaches every relationship through the markers,
  rows and lattice cells; hover only adds emphasis.
- **Lattice gate** (`ProvenanceMap`): rows are the claims, columns the exhibits they cite —
  built from `answer.claims × answer.evidence` and nothing else. It renders only when some
  claim cites two exhibits or some exhibit backs two claims (and there are ≥ 2 of each).
  Row headers are the claims' opening words; cells are named "Claim 4 cites exhibit 1, DR-112"
  (empty ones "doesn't cite"). Activating a cell opens that exhibit. On phones it starts
  folded to "Claims and exhibits · 4 claims · 4 exhibits".
- **Exhibits** (`Exhibits`): ruled rows on the sheet with the marker in the margin, type
  glyph, title (two lines at most), a ledger line of source facts (D-8: an account only when
  the API supplies one), the excerpt in the record voice with the first verified quote in
  marker tint, and notes only from fields: sensitivity, "Cited by N claims", "In a potential
  conflict", "Replaced by / Replaces …, as recorded by the source". Undated items show
  "Updated <date>" rather than passing the update date off as the source's own. Readable
  evidence the AI data policy kept from the model is one group under one hatched edge;
  "Also retrieved" stays collapsed.
- **Inspector** (`EvidenceInspector`, `ExhibitViewer`): header (marker, type, "2 of 6",
  ←/→, close), the provenance trail `source › reference › section › v1 › chars 743–768 ›
  ✓ quote verified` (verified only when this answer's quote is found in the text), the
  title, the passage on the sheet with each quoting paragraph barred in verdigris and the
  first quote scrolled into view inside the panel, *Cited by* (every citing claim, each with
  *Show* back to its marker), source details as a two-column ledger, *Open in source* /
  *Copy link*. `[`, `]`, `Esc`; focus moves in on open and back on close. Docked ≥1280 px
  (no page scroll on open), drawer 768–1279, bottom sheet on phones; *Show* from a drawer
  closes it and lands on the marker.
- **Disagreement**: potential conflicts use the D2 `DisagreementAxis` with every position
  at equal width and the API's summary as the only conclusion; supersession is the neutral
  form, built from the conflict's recorded link and the two documents' own dates, with
  *Inspect earlier* / *Inspect replacement*. The axis primitive now omits an empty axis and
  goes vertical beyond three positions (and on phones); intervals use grouped digits
  ("1,308 days later").
- **States**: every status keeps its logic and copy, in the §6.10 families — insufficient
  and unsupported are quiet (with the searched scope as a ledger), the policy case of
  evidence-only is a plain statement over the hatched group, failed and request errors are
  faults with references and *Try again*, and a rate limit is a **wait** (`role="status"`,
  "Try again in 30 seconds", question kept).
- **Trace** (`TraceScreen`): the question as the title with the brief's ledger line; the
  scope sent as a sentence; the pipeline as numbered stops on one rule (vertical below
  1024 px; skipped stops hatched, failed ones in fault), rejection codes as a ledger; the
  answer as recorded through the brief's own components (markers open the inspector); the
  exhibits by title, each a separate re-authorized `GET /v1/evidence/{id}` — one no longer
  readable shows a uniform "This evidence isn't available now." row; run details; the raw
  record. The administrator's view shows only the operational stops under a hatched band
  "Question, answer and evidence aren't visible to administrators" and fetches no evidence.
- **Evidence page**: the viewer as a page (h1 title, trail, passage, details) on the sheet
  under an ink rule, with the back trail. Not available stays identical for nonexistent,
  unreadable and malformed IDs, and nothing echoes the requested ID.

**Removed (v1):** `AskIntro` (the three principles), `AskPage.module.css`, `Citations`
(chip and claim span), `EvidenceList`, `StateBlocks`, `States.module.css`, `RequestError`
(now in `AnswerStates`), `ScopeControls` (the scope chips), `EvidenceBody`, `scopeLine` and
its tests, the trace's ID-only reference list and its stage list. The raw-px Stylelint rule
now covers `ask`, `evidence`, `trace` and `record`.

| Planned (§5–§6.5) | As built | Why |
| ----------------- | -------- | ------- |
| Lattice "under the status line" | After the claims, disagreement and unknowns, directly above the exhibits it maps | The reader meets the answer first; the grid then bridges the two sections it relates |
| Lattice rows "Lead", "1", "2" | Row headers are the claims' opening words | Bare numbers forced a lookup; claims have no visible numbers elsewhere |
| Inline markers after each sentence *and* margin markers | One set: margin at ≥768 px, inline after the claim on phones; one claim per row | Two buttons per relationship doubled Tab stops and cluttered the prose |
| Scope token "as **Internal**" | "as **default sensitivity**" | `/v1/me` doesn't return the organization's default level; the UI doesn't guess |
| Progress line names the sources | Names how many readable sources are searched | Listing names again duplicates *Your reach* on a screen that just showed it |
| Exhibit viewer "metadata as a two-column ledger" | Plus *Cited by* with *Show* back to each claim's marker | The inspector→claim direction of the provenance chain |
| Trace exhibits (IDs only today) | Titles from each exhibit's own re-authorized read | The stored answer carries IDs only; each read is the same one opening it would make |
| Hatched egress state callout + hatched rows | One hatch per boundary: the withheld group's edge (and the notice's edge) | Repeated hatching read as decoration |

**Accessibility.** Every relationship is reachable without hover (markers, rows, lattice
cells); markers are 24 px on touch with 6 px gaps; the lattice is one Tab stop with arrow,
Home and End keys and named cells; status changes are announced once ("Answer ready:
Answered, 3 claims."), the rate limit as a status, failures as alerts; reduced motion
removes the arrival and the quote sweep; forced colours keep the dashed boundary edge and
the selected marker; 200 % zoom (640 CSS px) and 320 px reflow without page scroll.

**Security invariants checked.** Lattice cells, markers and rows come only from the
response (no marker, cell or row for absent evidence — e2e); D-8 (no account row or trail
segment when the API omits it, even if the text names the account); D-13 (the question's
sensitivity is only raised, and shown); hatching only for readable evidence policy kept
from the model and the not-available state; nonexistent = unauthorized for evidence and
traces (unchanged tests, plus the inspector); trace re-authorization unchanged (the hidden
answer is never refilled from the Ask page; revoked trace evidence shows one uniform row);
the administrator's trace fetches no evidence.

**Defects found by the new tests and review, and fixed:**
- A rate limit (and any failed request) was announced twice: by the page's live region
  and by its own state block. The block now speaks alone.
- Tabular figures in the ledger voice carry tabular punctuation, so lattice row headers
  read "June , Halden"; claim openings now use proportional punctuation.
- Undated sources showed their last-update date as if it were the source's date; rows now
  say "Updated …".
- The policy state drew three hatches (state, group, every row); now one per boundary.
- `composes` inside descendant or grouped selectors broke the production build (CSS
  Modules allow it only on a single class); replaced with explicit role declarations.

**Verification.** Local gate, run sequentially: lint (ESLint + Stylelint), typecheck,
128 unit tests, production build, and the full Playwright run with one worker (368 passed,
45 skipped by project: desktop-only / phone-only), including the new `brief.spec.ts`
(desk, keyboard scope, date validation, partial, insufficient, withheld, no-model,
rate limit, supersession, conflicts, one-to-one vs many-to-many lattice, claim → exhibit
and exhibit → claim, inspector keyboard and *Show*, long answers at 1440–320 px, 200 %
zoom, reduced motion, forced colours, trace, revoked trace evidence, evidence page, D-8)
and `ask-mobile.spec.ts` (inline markers, folded lattice, vertical axis, sheet inspector,
*Show* from the sheet, 320 px), plus the existing safety, keyboard, axe and shell suites. Reviewed at
1440/1280/1024/768/390: desk, pending, answered, many-to-many, inspector (docked, drawer,
long exhibit), conflicts, many exhibits, partial, evidence-only, rate limit, supersession,
failed, insufficient, ambiguous, unsupported, trace (asker, administrator, hidden),
evidence and not-available.

### D5 — Sources, Activity, Access and AI providers (2026-10-03, `phase/design-overhaul`)

**Built.** The operational and administrative surfaces on the D1–D4 system, each with its
own structure. Frontend only: no API, schema, authorization or backend change.

- **Shared frame only** (`components/ops/OpsPage`): the admin width, a title with one
  sentence under it, the back trail of a deep page, a view switch between sibling pages
  (`ViewTabs`: Members · Audit log; Your questions · Organization runs), the administrator
  gate, and a section heading. What sits under the head is each page's own structure.
- **Sources — a live register** (`SourcesScreen`, `sync-model`). One headline sentence
  built from the listing ("3 sources: 1 syncing now · 1 with failed records · 1 last fully
  synced over a week ago · 1 never synced.") instead of stat cards, then a DataTable:
  source (type glyph, name, key · connector, "Paused: not synced" when inactive), last sync
  (status word + icon + finish time; a failed or interrupted run is the only fault, with
  its code; a partial run is a warning icon, not red; the age of the last *full* sync only
  when it adds information, in the time signal after a week), permissions (sentence form,
  when access was last applied, default sensitivity), documents, failed records (count,
  and an inline expansion with stage · code and each record by opaque reference) and
  *Sync now*. Members get the same anatomy with "N documents you can read" and no action.
  - *Sync now* keeps the real request. While pending, queued or running the button stays
    focusable but inert (`aria-disabled`), the state is written beside it ("Queued since
    09:10", "Syncing since 09:10", "Requested sync failed · code"), the listing is polled,
    and the request's progress is announced in a polite live region derived from the
    listing itself (requested → syncing → finished: outcome). A refused request is an
    alert in the row. The toast only echoes.
- **Activity — a chronology** (`ActivityScreen`). Your questions grouped by UTC day
  (Today / Yesterday / weekday date, with the date and count), each at its time on a ruled
  rail: status word, the question (opens its re-authorized trace) and *Ask again* (back in
  the composer, unsent) — the same concept, words and destinations as Jump and the Ask
  desk (`activity/questions.ts`). Kept to a reading width. Empty state says what Activity
  records. **Organization runs** moved to its own page, `/activity/organization`
  (administrators; also in Jump): a dense DataTable — when, member (links to their access),
  status and code, model, duration, tokens, run details — under one boundary band
  ("Questions, answers and evidence aren't visible to administrators").
- **Access — members** (`AccessScreen`; a table down to 768 px, stacked on phones): *Find a member* (D2 Field + SearchInput; the
  API's own `q` search, announced as "3 members shown"), then a DataTable: member (name,
  e-mail in the ledger voice, role · Administrator), status (Active; Suspended / Removed
  with an icon; "Managed by your identity provider"), clearance as a LevelMeter, accounts,
  departments. No results is a quiet state naming what was typed.
- **Access — a member** (`MemberScreen`): identity (name, e-mail, role, status,
  administrator, identity-provider tag); the explanation as the page's lead in the record
  voice (the accepted access sentences); actions or the refusal note (self, removed,
  identity-provider lifecycle); the last write's result; then **How this access is made
  up** — a ruled ledger of rule · value · what it gives (clearance, accounts, departments,
  groups and channels with their origin and members, direct grants). Hidden private groups
  and grants on unreadable documents are one inline boundary each — no name, id or count.
  A suspended member keeps the ledger under a line saying none of it is in effect.
- **Change access — Now → After** (`ChangeAccess`, `compare.ts`). Step 1 edits with the
  D2 field family (radio and checkbox groups with help; a filter beyond eight choices; a
  live "2 rules changed"). Step 2 is the review, built from the **preview endpoint's
  effects** (the server's reading, not the form's): one row per rule — Now, After, items
  added (+) or removed (−, struck through), and the direction in words (Gains / Loses /
  Gains and loses) with a verdigris or vermilion margin bar — then the effect sentences,
  the exact counts the API returns ("Among documents you can read: Gains 7 · Loses 3"),
  the required reason (Field + TextArea) and *Apply change*. Focus moves to the review
  heading, *Back* returns to *Review change* with the edit kept. A 409 is an alert with
  *Close and review the latest* and Apply disabled. Suspend / Reactivate go straight to the
  review (Suspend is the danger variant). Every write leaves a persistent result line on
  the page (`role="status"`, the time, the audit phrases, a link to the audit log); the
  toast only echoes.
- **Grants**: document search on Field + SearchInput with Radio results (only documents
  the reviewer can read), the verdict (can / can't, the deciding reasons), reason fields,
  Revoke as the danger variant.
- **Audit — an immutable ledger** (`AuditScreen`): "Entries can't be edited or deleted"
  with a lock, then days under ink rules; each entry is time (ledger voice, seconds) on the
  rail · action and subject · the change (effects, field-by-field details with values in
  the ledger voice, or the document) · actor (member, operator tool or identity provider,
  each with its icon) and the quoted reason. Keyset paging ("Show older changes", then
  "No older changes."). No filters: the API has none.
- **AI providers — the trust boundary** (`ProvidersScreen`, `egress-model`): a hatched
  read-only band naming the audited commands (and linking the audit log); the policy as one
  ruled line; then **What may leave Lattice** — the egress lattice, levels × recorded
  providers grouped Generation / Embedding, every cell either filled (may be sent) or
  hatched (not sent), each a named button ("Confidential data isn't sent to gemini ·
  gemini-3.5-flash-lite: the AI data policy allows external APIs only up to Internal.");
  one Tab stop, arrows / Home / End, the focused or hovered cell explained in a line under
  it, Enter pins it. Then which levels never leave Lattice, and the provider ledger
  (trust class with its meaning, approval as a LevelMeter, status, masked credential, data
  terms). The cells follow the server's decision order using only returned fields; a
  credential is not an input (tested).

**Primitive changes.**
- `Lattice`: `focusable="all"` (every non-empty cell is a keyboard stop), `colGroups`, and
  `ground` (paper / sheet) so its row headers, now sticky inside a scrolling lattice, match
  the surface under them.
- `DataTable`: `short` columns keep label and value on one line when stacked on phones;
  the name column never narrows below 11rem while the table is a table.
- `Lattice` and `DataTable` scroll regions are positioned (see defects).
- `Button`: `loading` is now `aria-busy` + `aria-disabled` with clicks ignored, not the
  native `disabled` attribute (see defects); busy keeps the variant's look, danger too.
- `Field`: the visually hidden "(required)" is gone — the control's own `required` is
  announced; the name stays "Reason".
- Formatting: `formatTime` (± seconds), `daysAgo`, `dayHeading`, `ageLabel`, `dayKey`;
  `lib/chronology.groupByDay`; `lib/use-now` (ages read once per view).
- Navigation: subpages may require a capability; *Organization runs* is one.

**Removed (v1).** The four page tables and their stacked card lists (Sources ×2, Activity,
Access, Providers) → DataTable; `Access.module.css` (669 lines) and the page modules
rewritten; `AccessFrame` → `AdminGate`; `ReasonField` and both ad-hoc search inputs →
Field / TextArea / SearchInput; the raw radio/checkbox lists → ChoiceGroup / Radio /
Checkbox; the `Skeleton` primitive (grey bars; nothing used it any more — loading is rule
skeletons and DataTable's own rows); the old section labels on these pages. `SectionLabel`
remains only on sign-in (D6). The raw-px Stylelint rule now covers `ops`, `sources`,
`activity`, `access` and `settings`.

| Planned (§6.6–§6.9, D5 brief) | As built | Why |
| ----------------------------- | -------- | --- |
| Sources: a freshness *bar* | Words: the age of the last full sync, in the time signal after a week, shown only when it adds information | A bar's length reads as a score; the API gives one timestamp |
| Failures expand inline | Inline, under the row, with stage · code totals and each record | As planned |
| Activity: status filter (client-side) | Not built | Filtering one loaded page misleads once there are older pages |
| Organization activity: a hatched question cell on every row | One boundary band over the runs | Hatching every row was decoration (the D4 lesson: one hatch per boundary) |
| Organization activity on the Activity page | Its own page, `/activity/organization`, behind a view switch | Two different questions (mine vs the organization's runs) get two structures |
| Member: access lattice (levels × reach) | A ruled ledger: rule · value · what it gives | A levels × accounts grid would compute authorization in the browser; the D5 brief forbids that and forbids a lattice for its own sake |
| Egress lattice: a "Stays in Lattice" column | A sentence naming the levels no provider may receive, plus the readers rule | A column that is always filled is decoration |
| Provider credential problems in ochre | A warning icon and words, no signal colour | Ochre means time; a broken reference isn't a time state |
| Audit: filters | None | The audit endpoint has no filter parameters |

**Accessibility.** Tables keep explicit roles when stacked; chronologies are headed lists;
every row action is a named button reachable by Tab in reading order; busy buttons keep
focus; sync progress and write results are announced once (status), refusals are alerts;
dialogs move focus to the review and back, and return it to their opener; the egress
lattice is one Tab stop with named cells; colour never carries a state alone (icons and
words for run status, member status, gain / loss, permitted / not sent; dashed vs solid
cells, kept in forced colours); nothing animates under reduced motion; 200 % zoom (640 CSS
px) and 320 px reflow without page scroll; 44 px touch targets on phones.

**Security and privacy invariants checked.** Administrator ≠ content: organization runs
show no question, answer or evidence (asserted), the administrator trace is unchanged.
D-17: no change, suspend, grant or revoke control on your own page (asserted; the API
still refuses). D-18: private groups and unreadable grants appear only as one
acknowledgement each, never named, listed or counted (asserted, unit and browser). Audit:
only what the API returns is shown; pages follow its cursor only; a removed member is "A
former member" without a link. Members are found only through the existing `q` search;
non-administrators get one not-available page and no request is made. Lifecycle of
identity-provider members stays with the identity provider. Counts appear only as the
preview returns them. The egress lattice adds no permission: credentials don't change a
cell (asserted), unknown classes fail closed, the readers rule is stated.

**Defects found by the new tests and fixed.**
- `Button`'s `loading` used the native `disabled` attribute, so focus fell to the page mid
  request (Sync now, Apply change, Grant, Revoke).
- Visually hidden labels inside a scrolling `Lattice` or `DataTable` escaped the scroll
  region (no positioned ancestor) and widened the page once it scrolled; found by the
  eight-provider lattice at 390 px.
- A future-dated event (a clock ahead of the browser's) was headed "Today"; days ahead now
  get their date.
- `Field` named required controls "Reason (required)" while also announcing `required`.
- `composes` in a descendant selector broke the production build again (D4 lesson).
- At 1024 px fixed column widths crushed the Sources name column to one letter per line
  (and cramped the provider ledger); found in the five-width review, not by a test.
- A first take on the sticky lattice headers set their ground as a `:root` token, which
  froze it to the light paper; the dark-map specimen's axe pass caught it.

**Contract observations (no change made; none blocks the approved UX).**
- `GET /v1/admin/providers` returns the recorded configuration, not each provider's
  effective level or the implementation-declared trust class the server also compares.
  A filled cell is therefore what the recorded configuration allows; the server can still
  refuse at call time (a declared-class mismatch, a missing credential), never send more.
- `GET /v1/activity` doesn't say which traces would now hide their answer; Activity shows
  only the question and status, and the trace decides when opened.
- No list endpoint filters by status, clearance or audit action; the only filter built is
  the members `q` search the API already has.

**Verification.** Local gate, run sequentially: lint (ESLint + Stylelint), typecheck, 162
unit tests (was 128; new: `sync-model`, `compare`, `egress-model`, the Sources, Activity,
Access, Audit and Providers screens, time formatting), production build, and the full
Playwright run with one worker — 434 passed, 48 skipped by project (desktop-only /
phone-only), 0 failed. New or rewritten browser specs: `sources` (healthy / partial /
failed / never synced, the queued → running → finished sync with its announcements and an
inert, still-focused button, a refused request, failed records, empty, five widths and
320 px with long titles), `activity` (chronology, trace, the hidden-answer trace, *Ask
again*, empty, organization runs and the members' refusal, widths), `access` (review with
Now → After and exact counts, Back, conflict, self-change refusal, member search and no
results, grants, audit chronology, identity events, cursor paging, widths with forty
members and a long-named member), `access-mobile`, `providers` (read-only, the lattice's
keyboard walk and pinned explanation, hatched vs solid cells in forced colours, eight
providers at every width as a named scroll region), `operations` (200 % zoom on every D5
page, axe at rest, forced-colour signals, reduced motion, Tab order of row actions) and
`operations-mobile` (Sync now reachable above the tab bar with a 44 px target, the member
ledger, sticky lattice levels); the existing safety, identity, shell, keyboard and a11y
suites updated for the new names. Reviewed at 1440/1280/1024/768/390: Sources (healthy,
failed with records open, member, empty), Activity (populated, empty, organization runs),
Access (members with forty rows, member, long-named member, suspended, review with three
rules, applied result), audit (sixty events), providers (three and eight providers) and
the providers refusal; phone views of Sources, the member and providers. No backend,
API or shared contract changed, so backend CI was not run.
