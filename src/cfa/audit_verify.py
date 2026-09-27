"""Independent verification of the audit hash chain: ``make verify-audit``.

Runs with the read-only platform role. For every event, in ``seq`` order, it:

1. recomputes SHA-256 over the row's canonical form and compares it with ``hash``
   (catches any edited column);
2. checks ``prev_hash`` equals the previous event's ``hash`` (catches deleted,
   inserted or reordered events);
3. checks ``seq`` is contiguous from 1 (catches deletions at the head or middle).

Truncating the *tail* and re-hashing from there is only detectable against an
external anchor: the head hash printed here is what production would publish to
WORM storage (Azure immutable blob / Confidential Ledger, S3 Object Lock).
"""

import hashlib
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cfa.config import database_settings

GENESIS = "sha256:genesis"
_ROWS = (
    "SELECT e.seq, e.event_id::text AS event_id, e.trace_id, e.prev_hash, e.hash, audit.canonical(e) AS canonical "
    "FROM audit.events e ORDER BY e.seq"
)


@dataclass(frozen=True, slots=True)
class ChainBreak:
    seq: int
    event_id: str
    trace_id: str
    problem: str


@dataclass(slots=True)
class ChainReport:
    events: int = 0
    head: str = GENESIS
    breaks: list[ChainBreak] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.breaks


def digest(canonical: str) -> str:
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def verify_chain(rows: Iterable[Mapping[str, Any]]) -> ChainReport:
    """Pure: rows need seq, event_id, trace_id, prev_hash, hash and canonical."""
    report = ChainReport()
    expected_seq, expected_prev = 1, GENESIS
    for row in rows:
        seq = int(row["seq"])
        problems = []
        if seq != expected_seq:
            problems.append(f"sequence gap: expected #{expected_seq}, found #{seq} (events deleted or reordered)")
        if row["prev_hash"] != expected_prev:
            problems.append("broken link: prev_hash does not match the previous event's hash")
        if digest(str(row["canonical"])) != row["hash"]:
            problems.append("content altered: stored hash does not match the event's contents")
        report.breaks += [ChainBreak(seq, str(row["event_id"]), str(row["trace_id"]), p) for p in problems]
        report.events += 1
        expected_seq, expected_prev = seq + 1, str(row["hash"])
    report.head = expected_prev
    return report


def verify_database(conn: psycopg.Connection[Any]) -> ChainReport:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_ROWS)
        return verify_chain(cursor)


def main() -> int:
    dsn = database_settings().database_url.get_secret_value()
    with psycopg.connect(dsn) as conn:
        report = verify_database(conn)
    out = sys.stdout
    if report.ok:
        out.write(f"\033[32mOK\033[0m  audit chain intact: {report.events} events\n")
        out.write(f"    head {report.head}  (anchor this externally to detect tail truncation)\n")
        return 0
    out.write(f"\033[31mTAMPERED\033[0m  {len(report.breaks)} problem(s) in {report.events} events\n")
    for b in report.breaks[:20]:
        out.write(f"    #{b.seq}  event {b.event_id}  trace {b.trace_id}\n      {b.problem}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
