---
id: kestrel-account-brief
title: Kestrel Bank — account brief
author: Daniel Reyes
created: 2025-03-11T09:00:00Z
updated: 2026-08-20T14:35:00Z
account: kestrel-bank
authority: working
---
Maintained by Daniel Reyes (AM). CSM: Rachel Kim.

## Snapshot

- Regional bank, 2,900 employees. Uses Halden Flow for loan-operations workflows and
  nightly core-banking exports.
- Enterprise plan, 3,100 seats. ARR $142,000. Renewal **31 January 2027**.
- IdP: PingFederate (SAML). Kestrel pins signing certificates and refreshes IdP metadata
  manually — this is what made the 21 July incident hit them hard.

## People

- Martin Osei — IAM lead; our technical contact.
- Sunita Rao — VP Digital Channels; budget owner and renewal decision-maker.

## Open topics (August 2026)

- **July SSO incident follow-up.** Customer RCA delivered 28 July (HAL-4711). Sunita
  asked for service credits; one month credit applied in August.
- **Mobile login.** Kestrel wants IdP-initiated login for the mobile app.
- **SCIM.** Martin asked about Directory Sync timing; not a blocker for them.
- **Certificate notices.** Kestrel wants at least 30 days' notice before any future
  signing-certificate change.

## Relationship

Healthy before July; strained since the incident. Sunita now joins the monthly check-in.
