"""The synthetic evaluation dataset (data/synthetic, evals/golden): loading, the
expected-access oracle and deterministic lint.

This package reads **raw source fixtures** -- Markdown with front matter, Zendesk-style
ticket JSON Lines, a Slack export -- only to validate them and the golden set that
refers to them. It is not a connector and not ingestion: it writes nothing to the
database and derives record keys and parts only as far as validation needs
(``docs/connector-design.md`` defines how Phase 4 connectors will read the same files).
No model is involved anywhere.
"""
