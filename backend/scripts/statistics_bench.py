"""Time the relevance gate's ``term_statistics`` as real askers (F3, phase-13-followups).

Isolated evaluation databases only (with ``scripts/scale_corpus.sql`` for larger corpora).
The askers come from a CSV of ``organization_id,membership_id,email`` lines::

    psql -U postgres -d lattice -Atc "select m.organization_id||','||m.id||','||u.email
      from memberships m join users u on u.id = m.user_id" > members.csv
    MEMBERS=members.csv uv run --env-file <eval env> python scripts/statistics_bench.py [--explain]
"""

import asyncio
import os
import statistics
import sys
import time
import uuid

from lattice.authz import resolve_authorization_context
from lattice.db.engine import Database, create_engine
from lattice.retrieval.terms import term_statistics, term_statistics_statement

TERMS = [
    ["scim", "date", "ga"],
    ["refund", "window", "enterpris"],
    ["nps", "score"],
    ["sso", "outag", "kestrel", "caus"],
]


def askers() -> list[list[str]]:
    with open(os.environ["MEMBERS"]) as members:
        return [line.strip().split(",")[:3] for line in members if line.strip()]


async def main() -> None:
    app = Database(create_engine(os.environ["LATTICE_DATABASE_URL"], application_name="bench"))
    rows = askers()
    times: list[float] = []
    for i, (org, member, email) in enumerate(rows):
        org_id, member_id = uuid.UUID(org), uuid.UUID(member)
        async with app.tenant_snapshot(org_id) as session:
            ctx = await resolve_authorization_context(
                session, organization_id=org_id, membership_id=member_id
            )
            for terms in TERMS:
                await term_statistics(session, ctx, terms, [])  # warm
                started = time.perf_counter()
                for _ in range(3):
                    stats = await term_statistics(session, ctx, terms, [])
                times.append((time.perf_counter() - started) / 3 * 1000)
            if i == 0 and "--explain" in sys.argv:
                conn = await session.connection()
                compiled = term_statistics_statement(ctx, TERMS[0], []).compile(
                    dialect=conn.dialect, compile_kwargs={"render_postcompile": True}
                )
                params = tuple(compiled.params[k] for k in compiled.positiontup or ())
                plan = await conn.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS) " + str(compiled), params
                )
                print("\n".join(r[0] for r in plan.all()))
        print(email, "permitted chunks", stats.chunks)
    times.sort()
    print(
        f"term_statistics ms: p50 {statistics.median(times):.1f} "
        f"p95 {times[int(0.95 * len(times)) - 1]:.1f} max {times[-1]:.1f} (n {len(times)})"
    )
    await app.dispose()


asyncio.run(main())
