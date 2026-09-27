---
id: scim-ga-decision
title: "Decision: Directory Sync (SCIM) general availability moves to December 2026"
author: Tom Lindqvist
created: 2026-09-18T08:30:00Z
updated: 2026-09-18T14:00:00Z
authority: system_of_record
---
Status: **approved 18 September 2026**. Decision owner: Tom Lindqvist. Approvers: Sofia
Marchetti (Engineering lead, Identity), Oskar Lind (VP Engineering), Grace Whitfield (VP
Customer Success).

## Decision

Directory Sync (SCIM 2.0 provisioning for Okta and Entra ID) will be generally available
in the **2026.12 release, targeted for 8 December 2026**. A beta starts in November for
two customers: **Acme Corp from 9 November** (Okta, up to 500 users) and Brightwater
Health from 16 November (Entra ID).

For context: the H2 roadmap of 2 July listed September, and in August the identity
squad was working towards October.

## Why

Load testing showed the v1 design (SCIM on the sync engine) cannot meet deprovisioning
latency or ordering requirements at Enterprise scale. We are rewriting the directory sync
engine as a dedicated provisioning service (RFC of 8 September), which takes about eight
weeks.

## Customer impact

- Acme Corp and Brightwater Health were told in August that SCIM was expected in
  October. Their account managers will contact them by 30 September.
- Other customers who asked (including Kestrel Bank) get the standard support macro.

## Communication rules

- Customer-facing dates are exactly those in this record: beta from November for the
  named customers, GA targeted for 8 December.
- Support uses the updated Directory Sync macro and does not give other dates.
- No contractual commitments to these dates without VP Customer Success and legal review.
