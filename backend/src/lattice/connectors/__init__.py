"""Connectors: source interpretation and normalization only (docs/connector-design.md).

They never touch the database, compute embeddings, call a model, build queries or
decide access. See ``lattice.connectors.contract``.
"""
