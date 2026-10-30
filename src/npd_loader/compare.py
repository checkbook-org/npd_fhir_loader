"""Compare one release's published row counts in two NPD databases: a manual check after a test deployment."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql

from npd_loader.db import list_parent_tables, list_release_partitions


@dataclass(frozen=True)
class CountRow:
    table: str
    this: int | None    # None: the release has no partition of this table here
    other: int | None

    @property
    def matches(self) -> bool:
        return self.this is not None and self.this == self.other


def release_counts(conninfo: str, schemas: list[str], release: date) -> dict[str, int]:
    """Exact row counts of every published partition of `release`, keyed by "schema.parent"."""
    counts = {}
    with psycopg.connect(conninfo) as conn:
        for schema in schemas:
            for parent in list_parent_tables(conn, schema):
                partition = list_release_partitions(conn, schema, parent).get(release)
                if partition is not None:
                    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, partition))
                    counts[f"{schema}.{parent}"] = conn.execute(query).fetchone()[0]
    return counts


def compare_counts(this: dict[str, int], other: dict[str, int]) -> list[CountRow]:
    return [CountRow(t, this.get(t), other.get(t)) for t in sorted(set(this) | set(other))]


def format_comparison(rows: list[CountRow], this_name: str, other_name: str) -> str:
    def n(value: int | None) -> str:
        return "-" if value is None else f"{value:,}"
    width = max([len("table")] + [len(r.table) for r in rows])
    lines = [f"{'table':<{width}}  {this_name:>15}  {other_name:>15}  match"]
    lines += [f"{r.table:<{width}}  {n(r.this):>15}  {n(r.other):>15}  {'yes' if r.matches else 'NO'}"
              for r in rows]
    return "\n".join(lines)
