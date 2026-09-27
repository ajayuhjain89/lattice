---
id: support-escalation-runbook
title: Support escalation runbook
author: Hannah Cho
created: 2025-02-10T09:00:00Z
updated: 2026-02-02T11:30:00Z
departments: [support]
authority: working
---
For the support team. Owner: Hannah Cho (support team lead).

## Tiers

- **Tier 1** triages every new ticket within the SLA first-response target, gathers
  workspace ID, correlation IDs and timestamps, and solves how-to questions.
- **Tier 2** (Priya Natarajan, Ben Adeyemi and others) handles SSO, connectors,
  webhooks and anything needing logs.
- **Engineering** is engaged through the on-call rotation for P1s and through a Jira
  escalation for reproducible bugs.

## When to escalate to Engineering

- Any P1, immediately (the SLA policy pages on-call automatically).
- SSO failures affecting more than one user in a workspace.
- Data that looks wrong or missing after a flow run.

## Roadmap and date questions

Customers often ask when a feature will ship. Use the macro for the feature if there is
one. Otherwise say it is on the roadmap and that the account manager will follow up;
tag the ticket `roadmap-question` and add the AM as a follower. Do **not** give dates
from Slack or from conversations with engineers.

## Refund and credit questions

Support cannot approve refunds or credits. Link the current Enterprise refund policy in
an internal note and route the ticket to the account manager.

## Customer mentions another customer

Never confirm which other customers are affected by an incident or use a feature.
