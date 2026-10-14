"""Cash conservation and idempotent reservations for segregated currency pools."""

from datetime import datetime

from t1fx.domain import Bucket, OpeningCash, Posting


class Ledger:
    def __init__(self, opening: tuple[OpeningCash, ...]):
        self.opening = {item.bucket: item.amount_minor for item in opening}
        if len(self.opening) != len(opening):
            raise ValueError("Duplicate opening cash bucket")
        self.balances = dict(self.opening)
        self.reservations: dict[str, tuple[Bucket, int]] = {}
        self.postings: dict[str, Posting] = {}
        self._operations: dict[str, tuple] = {}

    def free(self, bucket: Bucket) -> int:
        reserved = sum(amount for b, amount in self.reservations.values() if b == bucket)
        return self.balances.get(bucket, 0) - reserved

    def post(self, posting: Posting) -> bool:
        previous = self.postings.get(posting.posting_id)
        if previous is not None:
            if previous != posting:
                raise ValueError("Idempotency key reused for a different posting")
            return False
        if self.free(posting.bucket) + posting.amount_minor < 0:
            raise ValueError("Posting would spend unavailable/reserved cash")
        self.balances[posting.bucket] = self.balances.get(posting.bucket, 0) + posting.amount_minor
        self.postings[posting.posting_id] = posting
        return True

    def reserve(self, reservation_id: str, bucket: Bucket, amount_minor: int) -> bool:
        if type(amount_minor) is not int or amount_minor <= 0:
            raise ValueError("Reservation amount must be a positive integer")
        identity = (bucket, amount_minor)
        if reservation_id in self._operations:
            if self._operations[reservation_id] != identity:
                raise ValueError("Reservation ID reused with different terms")
            return False
        if self.free(bucket) < amount_minor:
            raise ValueError("Insufficient unreserved cash")
        self.reservations[reservation_id] = identity
        self._operations[reservation_id] = identity
        return True

    def settle(self, reservation_id: str, at: datetime) -> bool:
        posting_id = f"settle:{reservation_id}"
        if posting_id in self.postings:
            return False
        if reservation_id not in self.reservations:
            raise ValueError("Unknown active reservation")
        bucket, amount = self.reservations[reservation_id]
        posting = Posting(
            posting_id=posting_id, bucket=bucket, amount_minor=-amount,
            effective_at=at, available_at=at, kind="SETTLEMENT", source_ref=reservation_id,
        )
        del self.reservations[reservation_id]
        try:
            self.post(posting)
        except Exception:
            self.reservations[reservation_id] = (bucket, amount)
            raise
        return True

    def assert_conserved(self) -> None:
        expected = dict(self.opening)
        for posting in self.postings.values():
            expected[posting.bucket] = expected.get(posting.bucket, 0) + posting.amount_minor
        for bucket in set(self.opening) | set(self.balances):
            if expected.get(bucket, 0) != self.balances[bucket] or self.free(bucket) < 0:
                raise AssertionError(f"Cash invariant failed for {bucket.key}")
