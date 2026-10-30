# Lattice demo walkthrough

Six questions that show what Lattice does, on the synthetic companies `make demo` loads:
**Halden**, a software company, and **Veridian Analytics**, a separate company in the same
installation. Each step names the person to sign in as. Change person with the avatar
menu (top right) → *Sign out*, then pick someone else on the sign-in page.

Every result below was checked against a fresh `make demo` (2026-10-30). Your exact
ranking can differ slightly if the data or configuration changes.

`make demo` runs without an answer model, so each question shows **the evidence the person
may see** ("Sources found — no AI model available"): passages with their source, date,
authority and sensitivity. Lattice's permissions, refusals and supersession all work
without a model. A drafted answer needs one (see *Drafted answers* at the end).

## 1. Same question, different access

Ask **"What discount did we approve for Kestrel's renewal?"**

- **Arjun Mehta** (CEO, Confidential clearance). The first source is *Kestrel Bank —
  renewal risk assessment*, marked **Confidential**: "Up to 8% discount on a two-year
  renewal if Kestrel signs by 15 December. Approved by Arjun…".
- **Daniel Reyes** (account manager for Kestrel, Internal clearance). The assessment is
  not there, and nothing says it exists. He gets the account brief, the account channel
  and the general discounting guidelines.

Permissions are applied inside the search itself: a document a person can't read is
never retrieved, so it can't leak into a result, a count or an answer.

## 2. Same ticket number, two companies

Ask **"What's the status of HAL-4821?"**

- **Priya Natarajan** (Halden, Support) gets Halden's HAL-4821: Acme's users are not being
  deprovisioned after an Okta deactivation.
- **Felix Wagner** (Veridian, Support) gets Veridian's own HAL-4821, a different ticket
  about a SCIM timeline.

Each company's data is kept apart by the database itself (row-level security), so the
same identifier can never cross between them.

## 3. A policy that was replaced

As **Priya Natarajan**, ask **"What is the first-response target for an urgent Enterprise
ticket?"**

Both *Support SLA policy — 2025* and *Support SLA policy — 2026* are found. Open
**Trace** (on the result) and the *Superseded* note reads: "Support SLA policy — 2026"
replaces "Support SLA policy — 2025", as recorded by its source. Lattice keeps the old
policy visible and marks it as replaced; it settles this only because the source itself
says so, never by guessing from dates.

The trace also shows the whole pipeline for the question: what was searched, what the AI
data policy held back, and why no model was called.

## 4. Old answers next to newer facts

As **Daniel Reyes**, ask **"Is audit log streaming to Splunk available yet?"**

The first source is *Release notes — Halden Flow 2026.09* (15 Sep 2026): audit log
streaming to Splunk is in beta. Below it is HAL-4750 from August, where Support answered
that it was "on our roadmap". Every passage carries its date, so the change is visible
instead of hidden behind one confident sentence.

## 5. The decision record first

As **Maya Okafor** (account manager for Acme), ask **"When will Directory Sync be
generally available?"**

The first three passages come from the approved decision record *Directory Sync (SCIM)
general availability moves to December 2026* (18 Sep 2026). Older material follows: the
June kickoff notes with Acme, and a September support thread. Each shows its source type
and authority (system of record, working document or conversation). With an answer
model, Lattice also states where these sources disagree, side by side, without picking a
winner.

## 6. A request it won't do

As **Maya Okafor**, ask **"Draft an email to Acme promising SCIM in October."**

Lattice refuses: "Lattice answers questions from your sources. It can't send, change or do
anything on your behalf, so nothing was done." The relevant sources are still listed. The
same holds for "Open a ticket…", "Book a meeting…", "Update HAL-4821…" and for reading
what other people asked Lattice. No model is called for these.

## Drafted answers

To see Lattice draft cited answers, configure a generation provider for an organization.
On these synthetic companies a free Gemini API key is enough: the AI data policy lets an
unpaid key receive synthetic data only. The steps are in the [runbook](runbook.md) §5
(the key) and §3 (`lattice provider configure --purpose generation …`). Each claim then
cites its passage, every quote is checked word for word against the source (a claim that
fails is removed), and disagreeing sources are shown side by side.

Real-model answer quality has **not** been measured for this project (phase-13-followups
§22); treat drafted answers as a demonstration, not a benchmark.

## Other things to try

- **Activity** (top bar): your own questions and their traces.
- **Sign in as Lena Brandt** (IT administrator): the *Access* pages explain in sentences
  what each member can read, preview a change before it applies, and record it in the
  audit log. Being an administrator gives Lena no access to content.
- **Search filters** under the question box: limit by source, account or date.
