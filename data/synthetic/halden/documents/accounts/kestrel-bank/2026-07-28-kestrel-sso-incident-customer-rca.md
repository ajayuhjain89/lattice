---
id: kestrel-sso-incident-customer-rca
title: Root cause analysis — SSO login failures on 21 July 2026 (prepared for Kestrel Bank)
author: Daniel Reyes
created: 2026-07-27T16:00:00Z
updated: 2026-07-28T09:15:00Z
account: kestrel-bank
authority: working
---
Prepared for Kestrel Bank by Daniel Reyes (Account Manager) and Sofia Marchetti
(Engineering Lead, Identity). Shared with Martin Osei and Sunita Rao on 28 July 2026 in
response to HAL-4711.

## Summary

On 21 July 2026, Kestrel Bank users could not sign in to Halden Flow through SSO from
**07:40 to 09:55 UTC (2 hours 15 minutes)**. About 3,100 Kestrel users were affected.
No data was lost or exposed.

## What happened

Halden rotates the certificate it uses to sign SAML requests once a year. The 2026
rotation ran at 07:40 UTC. Kestrel's PingFederate configuration pins our signing
certificate and does not refresh metadata automatically, so it rejected requests signed
with the new certificate. At 09:55 UTC we restored the previous certificate and logins
recovered immediately.

## Why it happened

- The rotation replaced the certificate outright instead of signing with both the old
  and new certificates during an overlap window.
- We announced the rotation by email to workspace admins only 24 hours in advance.

## What we are changing

1. Dual-signing with a 30-day overlap window for every future rotation — shipped in
   release 2026.08 (18 August).
2. In-product and email notice at least 14 days before any certificate change; Kestrel
   asked for 30 days and we have agreed to 30 days for their workspace.
3. Guidance for IdPs that pin certificates, including how to enable metadata refresh.

## Credits

Per the Enterprise agreement, Kestrel Bank receives a service credit equal to one month
of fees, applied to the August invoice.
