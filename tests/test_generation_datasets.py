import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from t1fx.cli import main
from t1fx.datasets import build_tables, export_parquet, generate_release, validate_dataset, validate_tables
from t1fx.domain import Event, EventType, Scenario
from t1fx.fixtures import instant
from t1fx.generation import GenerationConfig, generate_dataset, generate_day, load_config
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots
from t1fx.storage import digest
from t1fx.table_contracts import TABLES, read_parquet


@pytest.fixture
def config():
    original = load_config(Path("configs/generation/smoke.json"))
    return GenerationConfig.model_validate(original.model_dump() | {"trade_count": 80, "trading_days": 4, "start_date": date(2026, 10, 13)})


def test_seeded_generation_is_reproducible_and_changes_with_seed(config):
    first = list(generate_dataset(config))
    second = list(generate_dataset(config))
    assert first == second
    changed = list(generate_dataset(config.model_copy(update={"seed": config.seed+1})))
    assert first[0].scenario.trades != changed[0].scenario.trades
    assert sum(len(day.scenario.trades) for day in first) == 80
    assert {day.split for day in first} == {"train", "tuning", "calibration", "test"}
    assert len({trade.trade_id for day in first for trade in day.scenario.trades}) == 80
    assert {source.currency for day in first for source in day.scenario.funding_sources} == {
        "CAD", "EUR", "GBP", "JPY", "INR",
    }


def test_multiple_fx_sources_and_jpy_minor_units(config):
    jpy = config.fx_routes[0].model_copy(update={
        "profile_id": "jpy-usd-test", "sell_currency": "JPY",
    })
    mixed = config.model_copy(update={"fx_routes": (config.fx_routes[0], jpy)})
    scenario = generate_day(mixed, mixed.start_date, 40, "train").scenario
    assert {source.currency for source in scenario.funding_sources} == {"EUR", "JPY"}
    for source in scenario.funding_sources:
        matching = [leg for leg in scenario.fx_legs if leg.funding_source_id == source.source_id]
        assert sum(leg.sell_amount_minor for leg in matching) == source.capacity_minor
    jpy_leg = next(leg for leg in scenario.fx_legs if leg.sell_currency == "JPY")
    assert jpy_leg.sell_amount_minor == jpy_leg.buy_amount_minor


def test_calendar_coverage_can_end_at_final_recovery_day(config):
    expected = list(generate_dataset(config))
    final_day = expected[-1].scenario.ends_at.date()
    bounded = config.model_copy(update={
        "market": config.market.model_copy(update={"valid_to": final_day}),
    })
    assert list(generate_dataset(bounded)) == expected


def test_random_mechanisms_do_not_shift_unrelated_draws(config):
    first = generate_day(config, config.start_date, 20, "train")
    changed = generate_day(config.model_copy(update={"ssi_mismatch_rate": 1.0}), config.start_date, 20, "train")
    assert first.scenario.trades == changed.scenario.trades
    assert first.scenario.fx_legs == changed.scenario.fx_legs
    unrelated = lambda day: [e for e in day.scenario.events
                             if e.event_type not in (EventType.SSI_VALID, EventType.SSI_MISMATCH)]
    assert unrelated(first) == unrelated(changed)
    assert all(row["ssi_mismatch"] for row in changed.injections)


def test_fx_cutoff_holiday_and_closed_day_conventions(config):
    route = config.fx_routes[0]
    # Tuesday before and after cutoff: two versus three joint business days.
    assert route.expected_credit(instant("2026-10-13T20:00:00Z")).date() == date(2026, 10, 15)
    assert route.expected_credit(instant("2026-10-13T22:00:00Z")).date() == date(2026, 10, 16)
    holiday_calendar = route.sell_calendar.model_copy(update={"holidays": frozenset({date(2026, 10, 14)})})
    holiday_route = route.model_copy(update={"sell_calendar": holiday_calendar})
    assert holiday_route.expected_credit(instant("2026-10-13T20:00:00Z")).date() == date(2026, 10, 16)
    assert route.expected_credit(instant("2026-10-17T12:00:00Z")).date() == date(2026, 10, 21)


@pytest.mark.parametrize("side", ["sell_calendar", "buy_calendar"])
def test_fx_route_respects_payment_holidays_for_booking_and_value_date(config, side):
    route = config.fx_routes[0]
    route = route.model_copy(update={
        name: getattr(route, name).model_copy(update={
            "holidays": getattr(route, name).holidays - {date(2026, 11, 11)},
        }) for name in ("sell_calendar", "buy_calendar")
    })
    calendar = getattr(route, side).model_copy(update={
        "holidays": getattr(route, side).holidays - {date(2026, 11, 11)},
        "settlement_holidays": frozenset({date(2026, 11, 11)}),
    })
    route = route.model_copy(update={side: calendar, "value_lag": 1})
    assert calendar.is_business_day(date(2026, 11, 11))
    assert route.expected_credit(instant("2026-11-10T15:00:00Z")).date() == date(2026, 11, 12)
    # Booking on a closed payment day rolls first, then applies the value lag.
    assert route.expected_credit(instant("2026-11-11T15:00:00Z")).date() == date(2026, 11, 13)
    same_day = route.model_copy(update={"value_lag": 0})
    assert same_day.expected_credit(instant("2026-11-11T12:00:00Z")).date() == date(2026, 11, 12)


def test_profile_coverage_and_invalid_local_configuration_fail(config):
    with pytest.raises(ValueError, match="coverage"):
        config.fx_routes[0].expected_credit(instant("2029-01-01T12:00:00Z"))
    invalid = config.model_dump()
    invalid["decision_time"] = "12:00:00"
    with pytest.raises(ValueError, match="Decision"):
        GenerationConfig.model_validate(invalid)
    with pytest.raises(ValueError):
        GenerationConfig.model_validate(config.model_dump() | {"trade_count": 3})


def test_generated_snapshots_do_not_use_future_credit_events(config):
    day = generate_day(config, config.start_date, 20, "train")
    scenario = day.scenario
    first = build_snapshots(simulate(scenario), scenario.decision_at)
    events = [event.model_copy(update={
        "event_time": event.event_time+timedelta(hours=2),
        "available_at": event.available_at+timedelta(hours=2),
    }) if event.event_type == EventType.FX_CREDITED else event for event in scenario.events]
    modified = Scenario.model_validate(scenario.model_dump() | {"events": events})
    assert first == build_snapshots(simulate(modified), scenario.decision_at)


def test_weekend_repair_waits_for_next_open_settlement_window(config):
    config = config.model_copy(update={"opening_cash_fraction": 1, "funding_stress_rate": 0,
                                       "late_confirmation_rate": 0, "ssi_mismatch_rate": 0})
    scenario = generate_day(config, date(2026, 10, 15), 1, "train").scenario
    trade = scenario.trades[0]
    events = [event for event in scenario.events if event.event_type != EventType.SSI_VALID]
    repaired = instant("2026-10-17T15:00:00Z")  # Saturday, after Friday's deadline.
    events.append(Event(event_id="weekend-repair", event_type=EventType.SSI_VALID,
                        trade_id=trade.trade_id, event_time=repaired, available_at=repaired))
    scenario = Scenario.model_validate(scenario.model_dump() | {"events": events})
    outcome = simulate(scenario).outcomes[0]
    assert outcome.missed_contractual_deadline
    assert outcome.settled_at == instant("2026-10-19T13:00:00Z")


def test_dataset_splits_cash_conservation_and_truth_separation(config):
    tables, _, report = build_tables(config)
    assert report["trade_count"] == 80
    assert report["snapshot_count"] == 160
    partitions = list(report["partitions"].values())
    for previous, following in zip(partitions, partitions[1:]):
        assert previous["last_label_known_at"] < following["first_execution_at"]
    forbidden = {"delay_multiplier", "late_confirmation", "missed_fx_cutoff", "ssi_mismatch", "funding_stress"}
    for name, contract in TABLES.items():
        if not contract.private:
            assert not forbidden & {column.name for column in contract.columns}
    broken = copy.deepcopy(tables)
    broken["cash_balances"][0]["closing_minor"] += 1
    with pytest.raises(ValueError, match="conservation"):
        validate_tables(broken)
    leaked = copy.deepcopy(tables)
    leaked["snapshots"][0]["split"] = "test"
    with pytest.raises(ValueError, match="partition"):
        validate_tables(leaked)


def test_contracts_reject_duplicates_overflow_and_naive_timestamps(config):
    tables, _, _ = build_tables(config)
    row = tables["trades"][0]
    with pytest.raises(ValueError, match="duplicate"):
        TABLES["trades"].normalize([row, row])
    for update in [{"settlement_amount_minor": 2**63}, {"executed_at": "2026-10-13T14:00:00"}]:
        with pytest.raises(ValueError):
            TABLES["trades"].normalize([row | update])


def test_release_is_immutable_reproducible_and_detects_tampering(config, tmp_path):
    first = generate_release(config, tmp_path, "json")
    repeated = generate_release(config, tmp_path, "json")
    assert first == repeated
    assert len(list((tmp_path/"datasets").glob("dataset-*"))) == 1
    assert validate_dataset(tmp_path, first["dataset_id"]) == first
    directory = tmp_path/"datasets"/first["dataset_id"]
    assert (directory/"simulation_truth"/"injections.json").is_file()
    assert not (directory/"tables"/"injections.json").exists()
    (directory/"tables"/"outcomes.json").write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        validate_dataset(tmp_path, first["dataset_id"])


def test_cli_generation_and_validation(config, tmp_path, capsys):
    config_path = tmp_path/"config.json"
    config_path.write_text(config.model_dump_json())
    main(["--artifacts", str(tmp_path), "generate", "--config", str(config_path), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["trade_count"] == 80
    main(["--artifacts", str(tmp_path), "validate-dataset", "--dataset", report["dataset_id"]])
    assert json.loads(capsys.readouterr().out)["dataset_id"] == report["dataset_id"]


@pytest.mark.parametrize("mutation", ["privacy", "format", "table_set"])
def test_dataset_rejects_inconsistent_manifest_metadata(config, tmp_path, mutation):
    manifest = generate_release(config, tmp_path, "json")
    path = tmp_path/"datasets"/manifest["dataset_id"]/"manifest.json"
    if mutation == "privacy":
        manifest["tables"]["injections"]["private"] = False
    elif mutation == "format":
        manifest["parquet_status"] = "written"
    else:
        manifest["tables"]["unexpected"] = {}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="mismatch"):
        validate_dataset(tmp_path, manifest["dataset_id"])


def test_parquet_round_trip_and_json_export(config, tmp_path):
    pytest.importorskip("pyarrow", reason="PyArrow dependency unavailable in this environment")
    original = generate_release(config, tmp_path, "json")
    exported = export_parquet(tmp_path, original["dataset_id"])
    assert exported["dataset_id"] != original["dataset_id"]
    assert original["identity"]["table_hashes"] == exported["identity"]["table_hashes"]
    validate_dataset(tmp_path, exported["dataset_id"])
    directory = tmp_path/"datasets"/exported["dataset_id"]
    for name, entry in exported["tables"].items():
        assert digest(read_parquet(directory/entry["parquet"], TABLES[name])) == entry["canonical_sha256"]
    assert (tmp_path/"datasets"/original["dataset_id"]).is_dir()


def test_verified_semantic_reuse_still_hashes_files_and_checks_scenario_set(config, tmp_path, monkeypatch):
    import t1fx.datasets as datasets
    manifest = generate_release(config, tmp_path, 'json')
    dataset_id = manifest['dataset_id']
    expected = validate_dataset(tmp_path, dataset_id, reuse_verified=True)
    monkeypatch.setattr(datasets, 'validate_tables', lambda _: (_ for _ in ()).throw(AssertionError('Semantic work repeated')))
    assert validate_dataset(tmp_path, dataset_id, reuse_verified=True) == expected
    directory = tmp_path/'datasets'/dataset_id
    target = directory/'resolved_config.json'
    raw = target.read_bytes()
    target.write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='checksum mismatch'):
        validate_dataset(tmp_path, dataset_id, reuse_verified=True)
    target.write_bytes(raw)
    (directory/'scenarios'/'unexpected.json').write_text('{}')
    with pytest.raises(AssertionError, match='Semantic work repeated'):
        validate_dataset(tmp_path, dataset_id, reuse_verified=True)


def test_catalog_parsing_reuse_is_isolated_and_detects_changed_bytes(config, tmp_path):
    from t1fx.workflow import dataset_catalog_entry, dataset_scenarios, _parsed_dataset
    manifest = generate_release(config, tmp_path, 'json')
    dataset_id = manifest['dataset_id']
    first = dataset_catalog_entry(tmp_path, dataset_id)
    misses = _parsed_dataset.cache_info().misses
    second = dataset_catalog_entry(tmp_path, dataset_id)
    assert first == second
    assert _parsed_dataset.cache_info().misses == misses
    first['configuration']['seed'] = -1
    assert dataset_catalog_entry(tmp_path, dataset_id) == second
    _, scenarios = dataset_scenarios(tmp_path, dataset_id)
    scenario_id = scenarios[0].scenario_id
    scenarios.clear()
    assert dataset_scenarios(tmp_path, dataset_id)[1][0].scenario_id == scenario_id
    target = tmp_path/'datasets'/dataset_id/'resolved_config.json'
    target.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(ValueError, match='checksum mismatch'):
        dataset_catalog_entry(tmp_path, dataset_id)


def test_parquet_timestamp_conversion_preserves_microseconds_and_nulls(tmp_path):
    pytest.importorskip('pyarrow')
    from t1fx.table_contracts import Column, TableContract, write_parquet
    contract = TableContract('precision', (Column('schema_version','string'), Column('split','string'),
        Column('id','int64'), Column('at','timestamp',nullable=True)), ('id',))
    rows = [{'schema_version':'1.0','split':'train','id':i,'at':stamp} for i,stamp in enumerate([
        '1969-12-31T23:59:59.999999+00:00', '2026-11-11T05:30:00.123456+05:30', None])]
    path = tmp_path/'precision.parquet'
    write_parquet(path, contract, rows)
    assert read_parquet(path, contract) == contract.normalize(rows)


def test_jpy_settlement_uses_its_actual_minor_unit_exponent(config):
    from decimal import Decimal, ROUND_CEILING
    route=config.fx_routes[0].model_copy(update={'buy_currency':'JPY','sell_currency':'EUR'})
    scenario=generate_day(config.model_copy(update={'fx_routes':(route,)}),config.start_date,5,'test').scenario
    for leg in scenario.fx_legs:
        expected=int((Decimal(leg.buy_amount_minor)/Decimal('1.10')*100).to_integral_value(rounding=ROUND_CEILING))
        assert leg.sell_amount_minor==expected


def test_missed_cutoff_uses_route_local_booking_day(config):
    from datetime import time
    from zoneinfo import ZoneInfo
    from t1fx.calendars import local_instant
    route=config.fx_routes[0].model_copy(update={'timezone':'Asia/Tokyo','booking_cutoff':time(17)})
    scenario=generate_day(config.model_copy(update={'fx_routes':(route,), 'missed_fx_cutoff_rate':1}),config.start_date,10,'test').scenario
    trades={t.trade_id:t for t in scenario.trades}
    for leg in scenario.fx_legs:
        day=(trades[leg.trade_id].executed_at+timedelta(minutes=10)).astimezone(ZoneInfo(route.timezone)).date()
        assert leg.booked_at==max(trades[leg.trade_id].executed_at+timedelta(minutes=10),local_instant(day,route.booking_cutoff,route.timezone)+timedelta(minutes=30))


def test_generation_on_trading_day_with_closed_payment_system(config):
    day=date(2026,11,11)
    scenario=generate_day(config,day,10,'test').scenario
    assert scenario.trades[0].contractual_deadline_at.date()==date(2026,11,12)


def test_sparse_daily_cohorts_cover_all_routes_across_dataset(config):
    config=config.model_copy(update={'trade_count':80,'trading_days':40,'start_date':date(2026,3,2)})
    days=list(generate_dataset(config))
    assert all(len(day.scenario.trades)==2 for day in days)
    assert {leg.sell_currency for day in days for leg in day.scenario.fx_legs} == {r.sell_currency for r in config.fx_routes}
