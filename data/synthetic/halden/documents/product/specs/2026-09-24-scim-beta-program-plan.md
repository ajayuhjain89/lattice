---
id: scim-beta-program-plan
title: Directory Sync beta program plan
author: Tom Lindqvist
created: 2026-09-24T11:00:00Z
updated: 2026-09-24T16:40:00Z
authority: working
---
Owner: Tom Lindqvist. Engineering: Kofi Mensah, Wei Chen. Customer Success: Rachel Kim,
Chloe Martin.

## Participants

| Customer | IdP | Start | Scope |
| -------- | --- | ----- | ----- |
| Acme Corp | Okta | 9 November | Pilot group of up to 500 users; dedicated engineer (Kofi) |
| Brightwater Health | Entra ID | 16 November | One hospital, about 400 users |

Kestrel Bank asked to join (HAL-4930). Not included: we want their SAML issues closed
first. Prospects on a trial are not eligible.

## What beta customers get

- Users and groups provisioned and deprovisioned from their IdP.
- A shared Slack Connect channel with the identity squad.
- Weekly review of provisioning logs with their admins.

## Exit criteria for GA (8 December)

1. p95 provisioning lag under 2 minutes at 25,000 users in load tests.
2. Zero orphaned or mis-grouped accounts across both beta tenants for 14 consecutive days.
3. Deprovisioning completes within 5 minutes of the IdP change in 99.9% of cases.

If criteria are not met by 1 December, the GA date moves and Tom writes a new decision
record.
