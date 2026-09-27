---
id: eu-data-residency-decision
title: "Decision: EU data residency via a Frankfurt region"
author: Tom Lindqvist
created: 2026-08-05T09:30:00Z
updated: 2026-08-05T09:30:00Z
authority: system_of_record
---
Status: approved 5 August 2026. Decision owner: Tom Lindqvist. Approvers: Oskar Lind (VP
Engineering), Elena Petrova (CFO).

## Decision

Halden will offer EU data residency through a new AWS **eu-central-1 (Frankfurt)**
region. General availability is planned for **Q1 2027**. Existing workspaces can migrate
on request after GA.

## Why

Eleven open opportunities and four renewals list EU residency as a requirement. A
Frankfurt region costs less than a partner-hosted option and keeps one control plane.

## Consequences

- EU workspaces will not support the Salesforce Marketing Cloud connector at launch.
- Support needs EU-hours coverage for EU-region incidents from GA.

## Communication

Account managers may tell customers "planned for Q1 2027". Do not commit a month.
