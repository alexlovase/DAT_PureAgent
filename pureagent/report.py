"""Build a small electrical-error report from already investigated records.

This is a presentation gate, not an electrical checker or a proof of review.
The caller retains the original candidates, reviews, and coverage internally.
Refuted, undetermined, incomplete, or ambiguously identified items are not
converted into electrical errors and are not labeled as passes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence(value) -> bool:
    """Allow a short reference or structured evidence; reject empty payloads."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_evidence(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_evidence(item) for item in value)
    return False


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def build_report(candidates: Iterable[Mapping], reviews: Iterable[Mapping], coverage: Mapping) -> dict:
    """Return ``{run_status, scope, confirmed_errors}`` without mutating inputs.

    Candidate: id, investigator, why, recommendation; optional title/refdes/refs/
    severity are retained for confirmed errors. Review: candidate_id, reviewer,
    verdict, raw_connection_evidence, datasheet_evidence. Evidence belongs to the
    review and may refer to existing materials rather than repeating them.
    Review IDs and source claims are checked structurally, not authenticated.

    Review order is chronological: the last record for a candidate is current,
    including a later refuted/undetermined review. Duplicate candidate IDs are
    ambiguous and cannot be promoted.

    Coverage: investigated_count, total_count, full_board, scope. Zero effective
    investigations yields not_completed. Positive counts yield partial_review
    unless full_board is explicitly True and investigated_count == total_count
    > 0. completed_review describes work scope and never means all errors were
    found or all circuits passed. The caller-supplied scope is returned unchanged.
    """
    if not isinstance(coverage, Mapping):
        raise TypeError("coverage must be a mapping")
    investigated = _count(coverage.get("investigated_count"))
    total = _count(coverage.get("total_count"))
    if investigated == 0:
        run_status = "not_completed"
    elif coverage.get("full_board") is True and total > 0 and investigated == total:
        run_status = "completed_review"
    else:
        run_status = "partial_review"

    candidate_rows = [row for row in candidates if isinstance(row, Mapping)]
    ids = Counter(row["id"] for row in candidate_rows if _text(row.get("id")))
    latest_reviews = {}
    for review in reviews:
        if isinstance(review, Mapping) and _text(review.get("candidate_id")):
            latest_reviews[review["candidate_id"]] = review

    confirmed = []
    for candidate in candidate_rows:
        candidate_id = candidate.get("id")
        if not _text(candidate_id) or ids[candidate_id] != 1:
            continue
        review = latest_reviews.get(candidate_id)
        if review is None or review.get("verdict") != "confirmed":
            continue
        investigator = candidate.get("investigator")
        reviewer = review.get("reviewer")
        if not _text(investigator) or not _text(reviewer):
            continue
        if investigator.strip().casefold() == reviewer.strip().casefold():
            continue
        if not _text(candidate.get("why")) or not _text(candidate.get("recommendation")):
            continue
        if not _evidence(review.get("raw_connection_evidence")):
            continue
        if not _evidence(review.get("datasheet_evidence")):
            continue
        error = {"id": candidate_id}
        for key in ("title", "refdes", "refs", "severity"):
            if key in candidate:
                error[key] = deepcopy(candidate[key])
        error.update(
            why=candidate["why"],
            raw_connection_evidence=deepcopy(review["raw_connection_evidence"]),
            datasheet_evidence=deepcopy(review["datasheet_evidence"]),
            recommendation=candidate["recommendation"],
        )
        confirmed.append(error)
    return {
        "run_status": run_status,
        "scope": deepcopy(coverage.get("scope", "")),
        "confirmed_errors": confirmed,
    }
