---
id: identity-roadmap-2026-04
title: Identity & Access roadmap — April 2026
author: Tom Lindqvist
created: 2026-04-08T09:00:00Z
updated: 2026-04-08T09:00:00Z
authority: working
---
Identity & Access squad: Tom Lindqvist (PM), Sofia Marchetti (engineering lead), Kofi
Mensah, Wei Chen. This is a planning document; dates are targets, not commitments.

## Themes for 2026

Enterprise customers are moving to Okta and Entra ID and expect lifecycle automation,
not just SSO. Our biggest gap is provisioning: every Enterprise renewal this year has
asked about SCIM.

## Plan

| Item | Target | Notes |
| ---- | ------ | ----- |
| Directory Sync (SCIM 2.0 provisioning) | **Q3 2026** | Okta and Entra ID first; Google Workspace later |
| SAML signing certificate auto-rollover | Q3 2026 | Overlap window so IdPs can pick up the new certificate |
| Admin-enforced MFA for break-glass accounts | Q4 2026 | |
| Session policies per workspace | 2027 | |

## Directory Sync approach

Build SCIM endpoints on top of the existing sync engine so provisioning reuses its job
scheduling and retries. Kofi will write the design in May.

## Customers waiting

Acme Corp (Okta migration this summer), Brightwater Health (Entra ID), and
prospects that list SCIM as a hard requirement.
