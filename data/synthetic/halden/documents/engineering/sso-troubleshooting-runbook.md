---
id: sso-troubleshooting-runbook
title: SSO troubleshooting runbook
author: Sam Patel
created: 2025-03-03T10:00:00Z
updated: 2026-08-04T12:00:00Z
departments: [engineering, support]
authority: working
---
For on-call engineers and Tier 2 support.

## First checks

1. Get the correlation ID from the customer's error page (shown since 2026.08).
2. Search the SSO logs for it; the log line names the failing check.
3. Confirm which IdP the workspace uses and whether it pins our signing certificate.

## Common failures

- **"Assertion expired" / NotOnOrAfter errors.** Usually clock skew between the IdP and
  Halden. We allow 3 minutes of skew. Ask the customer to check NTP on their IdP servers.
- **"Signature validation failed".** The IdP is using an old copy of our metadata, or
  pins a certificate we rotated. Since 2026.08 we dual-sign for 30 days before rotation;
  outside that window, ask them to refresh metadata.
- **Login loop after IdP migration.** The customer's new IdP app sends a different
  NameID format. Compare with the old app; Halden expects email address format.

## Escalate to Identity on-call when

- More than one workspace shows the same failure within an hour.
- A signing certificate change is scheduled within the next 14 days.
