---
id: audit-log-streaming-prd
title: PRD — Audit log streaming
author: Tom Lindqvist
created: 2026-06-10T10:00:00Z
updated: 2026-06-24T16:00:00Z
authority: working
---
Owner: Tom Lindqvist. Engineering: Data platform squad.

## Problem

Enterprise security teams want Halden audit events in their SIEM within minutes. Today
they can only export audit logs to S3 in hourly batches, which several customers
(Brightwater Health, Kestrel Bank) say does not meet their monitoring requirements.

## Scope

- Stream audit events to Splunk (HTTP Event Collector) and Datadog Logs.
- Delivery latency under 60 seconds at p95.
- At-least-once delivery with event IDs for de-duplication.
- Per-workspace configuration in the admin console.

## Out of scope

Custom webhooks for audit events; filtering by event type (later).

## Rollout

Beta with two or three Enterprise customers first, then GA. Target: Q4 2026.

## Success metrics

Five Enterprise workspaces streaming within 60 days of GA.
