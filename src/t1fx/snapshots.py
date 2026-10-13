"""Observable snapshots, built only from records available at the decision time."""

from datetime import datetime

from t1fx.domain import EventType, RulePrediction, Snapshot
from t1fx.simulator import SimulationResult


def build_snapshots(result: SimulationResult, as_of: datetime) -> tuple[Snapshot, ...]:
    if as_of.tzinfo is None or not result.scenario.starts_at <= as_of <= result.as_of:
        raise ValueError("Snapshot time must be aware and within the available replay")
    visible = [
        event for event in result.events
        if event.event_time <= as_of and event.available_at <= as_of
    ]
    postings = [
        posting for posting in result.ledger.postings.values()
        if posting.effective_at <= as_of and posting.available_at <= as_of
    ]
    ranks = {"EXECUTED": 0, "ALLOCATED": 1, "CONFIRMED": 2, "AFFIRMED": 3, "INSTRUCTED": 4}
    snapshots = []
    for trade in result.scenario.trades:
        if trade.executed_at > as_of or trade.available_at > as_of:
            continue
        events = sorted(
            (event for event in visible if event.trade_id == trade.trade_id),
            key=lambda event: (event.event_time, event.sequence, event.event_id),
        )
        stage, ssi, settled = "EXECUTED", "UNKNOWN", False
        for event in events:
            if event.event_type.value in ranks:
                if ranks[event.event_type.value] > ranks[stage]:
                    stage = event.event_type.value
            elif event.event_type == EventType.SSI_VALID:
                ssi = "VALID"
            elif event.event_type == EventType.SSI_MISMATCH:
                ssi = "MISMATCH"
            elif event.event_type == EventType.SETTLED:
                settled = True
        observed_cash = result.ledger.opening.get(trade.bucket, 0) + sum(
            posting.amount_minor for posting in postings if posting.bucket == trade.bucket
        )
        snapshots.append(Snapshot(
            trade_id=trade.trade_id, as_of=as_of, horizon_at=trade.contractual_deadline_at,
            bucket=trade.bucket,
            remaining_obligation_minor=0 if settled else trade.settlement_amount_minor,
            instruction_state=stage, ssi_status=ssi, observed_settled=settled,
            minutes_to_affirmation_cutoff=(trade.affirmation_cutoff_at - as_of).total_seconds()/60,
            minutes_to_settlement_deadline=(trade.contractual_deadline_at-as_of).total_seconds()/60,
            pool_usable_minor=observed_cash,
        ))
    return tuple(snapshots)


def score_rules(snapshot: Snapshot) -> RulePrediction:
    """A transparent triage score, deliberately NOT a probability or SHAP explanation."""
    if snapshot.observed_settled or snapshot.as_of >= snapshot.horizon_at:
        return RulePrediction(
            trade_id=snapshot.trade_id, status="NOT_APPLICABLE", score=0, factors=(),
            recommended_action="Review the recorded settlement outcome.",
        )
    score, factors = 0, []
    action = "Monitor the trade until settlement."
    if snapshot.pool_usable_minor < snapshot.remaining_obligation_minor:
        score += 35
        factors.append("Observed pool cash is below this trade's obligation.")
        action = "Review the funding plan and expected FX receipts."
    if snapshot.instruction_state != "INSTRUCTED":
        score += 30
        factors.append("Settlement instructions are not yet observed as ready.")
        action = "Follow up on confirmation, affirmation, or instruction routing."
    if snapshot.ssi_status != "VALID":
        score += 30
        factors.append("Valid settlement instructions have not been verified.")
        action = "Validate or repair SSI before the settlement deadline."
    if score and snapshot.minutes_to_settlement_deadline <= 120:
        score += 5
        factors.append("The contractual deadline is within two hours.")
    return RulePrediction(
        trade_id=snapshot.trade_id, status="UNCALIBRATED_RULE_SCORE", score=min(100, score),
        factors=tuple(factors), recommended_action=action,
    )
