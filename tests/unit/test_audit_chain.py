"""The audit hash chain verifier catches edits, deletions and reordering."""

from typing import Any

from cfa.audit_verify import GENESIS, digest, verify_chain


def chain(*payloads: str) -> list[dict[str, Any]]:
    """Rows as the database would produce them (hash = sha256 of canonical, which embeds prev_hash)."""
    rows: list[dict[str, Any]] = []
    prev = GENESIS
    for seq, payload in enumerate(payloads, start=1):
        canonical = f'{{"prev_hash":"{prev}","seq":{seq},"payload":"{payload}"}}'
        row: dict[str, Any] = {
            "seq": seq,
            "event_id": f"e{seq}",
            "trace_id": "t",
            "prev_hash": prev,
            "canonical": canonical,
        }
        row["hash"] = digest(canonical)
        rows.append(row)
        prev = row["hash"]
    return rows


def test_intact_chain_verifies_and_reports_the_head() -> None:
    rows = chain("route", "deny", "answer")
    report = verify_chain(rows)
    assert report.ok
    assert (report.events, report.head) == (3, rows[-1]["hash"])


def test_empty_chain_is_ok() -> None:
    assert verify_chain([]).ok


def test_edited_event_is_detected() -> None:
    rows = chain("route", "deny", "answer")
    rows[1]["canonical"] = rows[1]["canonical"].replace("deny", "allow")  # a DBA hides a denial
    report = verify_chain(rows)
    assert [(b.seq, b.problem.split(":")[0]) for b in report.breaks] == [(2, "content altered")]


def test_rehashing_the_edited_event_breaks_the_next_link() -> None:
    rows = chain("route", "deny", "answer")
    rows[1]["canonical"] = rows[1]["canonical"].replace("deny", "allow")
    rows[1]["hash"] = digest(rows[1]["canonical"])  # attacker recomputes that row's hash too
    report = verify_chain(rows)
    assert [(b.seq, b.problem.split(":")[0]) for b in report.breaks] == [(3, "broken link")]


def test_deleted_event_is_detected() -> None:
    rows = chain("route", "deny", "answer")
    del rows[1]
    problems = {b.problem.split(":")[0] for b in verify_chain(rows).breaks}
    assert problems == {"sequence gap", "broken link"}


def test_reordered_events_are_detected() -> None:
    rows = chain("a", "b", "c")
    rows[1], rows[2] = rows[2], rows[1]
    assert not verify_chain(rows).ok
