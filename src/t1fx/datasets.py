"""Build, validate, and atomically publish typed research datasets."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path

from t1fx.generation import GenerationConfig, generate_dataset
from t1fx.pipeline import _code_hash
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots
from t1fx.storage import canonical_bytes, digest
from t1fx.table_contracts import TABLES, read_parquet, write_parquet


def flatten(record) -> dict:
    row = record.model_dump(mode="python")
    bucket = row.pop("bucket", None)
    if bucket:
        row.update(cash_pool_id=bucket["cash_pool_id"], currency=bucket["currency"])
    return row


def validate_tables(tables: dict[str, list[dict]]) -> dict:
    if set(tables) != set(TABLES):
        raise ValueError("Dataset must contain the complete table contract")
    for name, contract in TABLES.items():
        tables[name] = contract.normalize(tables[name])
    trades = {row["trade_id"]: row for row in tables["trades"]}
    legs = {row["fx_leg_id"]: row for row in tables["fx_legs"]}
    outcomes = {row["trade_id"]: row for row in tables["outcomes"]}
    if not trades or set(outcomes) != set(trades):
        raise ValueError("Every trade needs exactly one outcome")
    if {row["trade_id"] for row in tables["injections"]} != set(trades):
        raise ValueError("Injection diagnostics must cover each trade")
    for name in ("snapshots", "outcomes", "injections"):
        for row in tables[name]:
            trade = trades.get(row["trade_id"])
            if trade is None or (trade["scenario_id"], trade["split"]) != (row["scenario_id"], row["split"]):
                raise ValueError(f"{name}: invalid trade reference or partition")
    snapshot_ids = {row["trade_id"] for row in tables["snapshots"]}
    if snapshot_ids != set(trades):
        raise ValueError("Every trade must have an observable decision snapshot")
    for row in tables["snapshots"]:
        trade = trades[row["trade_id"]]
        if not trade["available_at"] <= row["as_of"] < row["horizon_at"]:
            raise ValueError("Snapshot violates the decision-time observation boundary")
        if row["reference_policy_id"] != "fixed-0":
            raise ValueError("Unsupported dataset reference policy")
    for row in tables["events"]:
        if row["event_time"] > row["available_at"]:
            raise ValueError("Event available before occurrence")
        target = legs.get(row["fx_leg_id"]) if row["fx_leg_id"] else trades.get(row["trade_id"])
        if target is None or (target["scenario_id"], target["split"]) != (row["scenario_id"], row["split"]):
            raise ValueError("Event has an invalid target")
    sources = {(row["scenario_id"], row["source_id"]): row for row in tables["funding_sources"]}
    committed = Counter()
    for leg in legs.values():
        key = (leg["scenario_id"], leg["funding_source_id"])
        source = sources.get(key)
        if source is None or source["currency"] != leg["sell_currency"]:
            raise ValueError("Invalid FX source commitment")
        committed[key] += leg["sell_amount_minor"]
        if committed[key] > source["capacity_minor"]:
            raise ValueError("FX source capacity exceeded")
    movements = Counter()
    for row in tables["postings"]:
        movements[(row["scenario_id"], row["cash_pool_id"], row["currency"])] += row["amount_minor"]
    balances = set()
    for row in tables["cash_balances"]:
        key = (row["scenario_id"], row["cash_pool_id"], row["currency"])
        balances.add(key)
        if row["opening_minor"]+movements[key] != row["closing_minor"] or row["closing_minor"] < 0:
            raise ValueError("Dataset cash conservation failed")
    if set(movements)-balances:
        raise ValueError("Posting references a missing cash pool")
    partitions = {}
    previous_label_horizon = None
    for split in ("train", "tuning", "calibration", "test"):
        subset = [row for row in trades.values() if row["split"] == split]
        if not subset:
            raise ValueError(f"Empty {split} partition")
        earliest = min(row["executed_at"] for row in subset)
        latest_label = max(outcomes[row["trade_id"]]["outcome_known_at"] for row in subset)
        if previous_label_horizon and earliest <= previous_label_horizon:
            raise ValueError("Partition embargo does not allow previous labels to mature")
        previous_label_horizon = latest_label
        partitions[split] = {"trade_count": len(subset), "first_execution_at": earliest,
                             "last_label_known_at": latest_label}
    failure_reasons = Counter(reason for row in outcomes.values() for reason in row["failure_reasons"])
    mechanisms = ("late_confirmation", "missed_fx_cutoff", "ssi_mismatch", "funding_stress")
    injection_counts = {mode: sum(row[mode] for row in tables["injections"]) for mode in mechanisms}
    return {"trade_count": len(trades), "snapshot_count": len(tables["snapshots"]),
            "failed_count": sum(row["missed_contractual_deadline"] for row in outcomes.values()),
            "censored_count": sum(row["censored"] for row in outcomes.values()),
            "failure_reasons": dict(failure_reasons), "injection_counts": injection_counts,
            "partitions": partitions, "reference_policy_id": "fixed-0"}


def build_tables(config: GenerationConfig):
    tables = {name: [] for name in TABLES}
    scenarios = {}
    for generated in generate_dataset(config):
        scenario = generated.scenario
        result = simulate(scenario)
        base = {"schema_version": "1.0", "scenario_id": scenario.scenario_id, "split": generated.split}

        def append(name, row):
            # Select declared columns; generator truth never enters public schemas.
            values = {**row, **base}
            tables[name].append({column.name: values[column.name] for column in TABLES[name].columns})

        for trade in scenario.trades:
            append("trades", flatten(trade))
        for leg in scenario.fx_legs:
            append("fx_legs", flatten(leg))
        for source in scenario.funding_sources:
            append("funding_sources", flatten(source))
        for event in result.events:
            append("events", flatten(event))
        for posting in result.ledger.postings.values():
            append("postings", flatten(posting))
        for opening in scenario.opening_cash:
            append("cash_balances", {"cash_pool_id": opening.bucket.cash_pool_id,
                                     "currency": opening.bucket.currency,
                                     "opening_minor": opening.amount_minor,
                                     "closing_minor": result.ledger.balances[opening.bucket]})
        # Both views use the same observable-state builder as the API's reference replay.
        eod = min(trade.affirmation_cutoff_at for trade in scenario.trades)-timedelta(hours=2)
        for as_of in (eod, scenario.decision_at):
            for snapshot in build_snapshots(result, as_of):
                append("snapshots", {**flatten(snapshot), "reference_policy_id": "fixed-0"})
        for outcome in result.outcomes:
            append("outcomes", {**flatten(outcome), "outcome_known_at": scenario.ends_at})
        for row in generated.injections:
            append("injections", row)
        for row in generated.counterparty_truth:
            append("counterparty_truth", row)
        scenarios[scenario.scenario_id] = scenario.model_dump(mode="json")
    report = validate_tables(tables)
    if report["trade_count"] != config.trade_count:
        raise ValueError("Generated trade count does not match the configuration")
    return tables, scenarios, report


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _directory(root: Path, dataset_id: str) -> Path:
    if not re.fullmatch(r"dataset-[0-9a-f]{20}", dataset_id):
        raise ValueError("Invalid dataset ID")
    return root/"datasets"/dataset_id


def publish_dataset(root, tables, scenarios, config, report, provenance, output_format="auto") -> dict:
    if output_format not in {"auto", "json", "both"}:
        raise ValueError("Dataset format must be auto, json, or both")
    available = importlib.util.find_spec("pyarrow") is not None
    parquet = output_format == "both" or (output_format == "auto" and available)
    if parquet and not available:
        raise RuntimeError("PyArrow is unavailable. Install .[parquet], or select --format json")
    schemas = {name: contract.description() for name, contract in TABLES.items()}
    table_hashes = {name: digest(rows) for name, rows in tables.items()}
    identity = {"config_hash": digest(config), "schema_hash": digest(schemas),
                "table_hashes": table_hashes, "scenario_hash": digest(scenarios),
                "report_hash": digest(report), "provenance": provenance,
                "formats": ["json", "parquet"] if parquet else ["json"],
                "parquet_writer_version": importlib.metadata.version("pyarrow") if parquet else None}
    dataset_id = f"dataset-{digest(identity)[:20]}"
    target = _directory(root, dataset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return validate_dataset(root, dataset_id)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent))
    files, table_entries = {}, {}

    def json_file(name, value):
        path = staging/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(value))
        files[name] = _hash_file(path)

    try:
        json_file("resolved_config.json", config)
        json_file("schemas.json", schemas)
        json_file("validation.json", report)
        for scenario_id, scenario in scenarios.items():
            json_file(f"scenarios/{scenario_id}.json", scenario)
        for name, contract in TABLES.items():
            directory = "simulation_truth" if contract.private else "tables"
            filename = f"{directory}/{name}.json"
            json_file(filename, tables[name])
            table_entries[name] = {"rows": len(tables[name]), "canonical_sha256": table_hashes[name],
                                   "json": filename, "private": contract.private}
            if parquet:
                parquet_name = f"{directory}/{name}.parquet"
                write_parquet(staging/parquet_name, contract, tables[name])
                # Check the actual round trip before publishing an immutable release.
                if digest(read_parquet(staging/parquet_name, contract)) != table_hashes[name]:
                    raise ValueError(f"Parquet round trip changed {name}")
                files[parquet_name] = _hash_file(staging/parquet_name)
                table_entries[name]["parquet"] = parquet_name
        card = (
            f"# Dataset {dataset_id}\n\nSynthetic research data; not observed market activity.\n\n"
            f"- Seed: {config['seed']}\n- Trades: {report['trade_count']}\n"
            f"- Snapshots: {report['snapshot_count']}\n- Deadline failures: {report['failed_count']}\n"
            f"- Censored trades: {report['censored_count']}\n- Reference policy: fixed-0\n"
            f"- Formats: {', '.join(identity['formats'])}\n\n"
            "Calendar holidays and FX cutoffs are explicitly assumed profiles, not official schedules. "
            "Only USD purchases are generated. Securities are assumed available. Each day is an "
            "independent cash pool episode; balances do not roll across days. Opening cash and fully "
            "committed FX receipts are separate resources. No optimizer intervention is applied.\n\n"
            "Partitions contain whole trading days. All labels in one partition mature before the "
            "first execution in the next. Keep all snapshots of each trade together. Observation "
            "timestamps govern feature access; events and outcomes contain future information.\n\n"
            "Never use simulation_truth/ as model input. Injection rates describe sampled mechanisms; "
            "realised failures also depend on repairs and cash shared within a pool.\n\n"
            "Canonical table hashes identify logical data. Parquet byte identity additionally depends "
            "on the recorded PyArrow version. See resolved_config.json and validation.json for rates, "
            "partition counts and mechanism counts.\n"
        )
        (staging/"data-card.md").write_text(card)
        files["data-card.md"] = _hash_file(staging/"data-card.md")
        manifest = {"schema_version": "1.0", "dataset_id": dataset_id, "status": "SUCCEEDED",
                    "identity": identity, "files": files, "tables": table_entries,
                    "summary": report, "parquet_status": "written" if parquet else "not_exported"}
        (staging/"manifest.json").write_bytes(canonical_bytes(manifest))
        try:
            os.rename(staging, target)
        except OSError:
            if not target.exists():
                raise
            return validate_dataset(root, dataset_id)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_dataset(root: Path, dataset_id: str) -> dict:
    directory = _directory(root, dataset_id)
    manifest = json.loads((directory/"manifest.json").read_text())
    if (manifest.get("dataset_id") != dataset_id or manifest.get("status") != "SUCCEEDED"
            or manifest.get("schema_version") != "1.0"
            or f"dataset-{digest(manifest['identity'])[:20]}" != dataset_id):
        raise ValueError("Dataset manifest identity mismatch")
    formats = manifest["identity"]["formats"]
    if formats not in (["json"], ["json", "parquet"]):
        raise ValueError("Unsupported dataset formats")
    parquet = "parquet" in formats
    if manifest.get("parquet_status") != ("written" if parquet else "not_exported"):
        raise ValueError("Dataset format status mismatch")
    if set(manifest["tables"]) != set(TABLES) or set(manifest["identity"]["table_hashes"]) != set(TABLES):
        raise ValueError("Dataset table set mismatch")
    if not {"resolved_config.json", "schemas.json", "validation.json", "data-card.md"} <= set(manifest["files"]):
        raise ValueError("Dataset metadata files missing")
    for name, checksum in manifest["files"].items():
        path = directory/name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Artifact path escapes dataset directory")
        if not path.is_file() or _hash_file(path) != checksum:
            raise ValueError(f"Dataset checksum mismatch: {name}")
    tables = {}
    for name, contract in TABLES.items():
        entry = manifest["tables"][name]
        if ("parquet" in entry) != parquet or entry.get("private") != contract.private:
            raise ValueError("Dataset table format or privacy mismatch")
        prefix = "simulation_truth" if contract.private else "tables"
        if entry["json"] != f"{prefix}/{name}.json" or entry["json"] not in manifest["files"]:
            raise ValueError("Unexpected table path in manifest")
        rows = contract.normalize(json.loads((directory/entry["json"]).read_text()))
        if (digest(rows) != manifest["identity"]["table_hashes"][name]
                or digest(rows) != entry["canonical_sha256"] or len(rows) != entry["rows"]):
            raise ValueError(f"Dataset table identity mismatch: {name}")
        if "parquet" in entry:
            if entry["parquet"] != f"{prefix}/{name}.parquet" or entry["parquet"] not in manifest["files"]:
                raise ValueError("Unexpected Parquet path in manifest")
            if digest(read_parquet(directory/entry["parquet"], contract)) != digest(rows):
                raise ValueError(f"Parquet contents differ from canonical data: {name}")
        tables[name] = rows
    if validate_tables(tables) != manifest["summary"]:
        raise ValueError("Dataset summary does not match its tables")
    if digest(json.loads((directory/"resolved_config.json").read_text())) != manifest["identity"]["config_hash"]:
        raise ValueError("Dataset configuration identity mismatch")
    if digest(json.loads((directory/"schemas.json").read_text())) != manifest["identity"]["schema_hash"]:
        raise ValueError("Dataset schema identity mismatch")
    scenarios = {path.stem: json.loads(path.read_text()) for path in (directory/"scenarios").glob("*.json")}
    if digest(scenarios) != manifest["identity"]["scenario_hash"]:
        raise ValueError("Dataset scenarios do not match their identity")
    if digest(manifest["summary"]) != manifest["identity"]["report_hash"]:
        raise ValueError("Dataset report identity mismatch")
    if json.loads((directory/"validation.json").read_text()) != manifest["summary"]:
        raise ValueError("Dataset validation report mismatch")
    return manifest


def generate_release(config: GenerationConfig, root: Path, output_format="auto") -> dict:
    if output_format == "both" and importlib.util.find_spec("pyarrow") is None:
        raise RuntimeError("PyArrow is unavailable. Install .[parquet], or select --format json")
    tables, scenarios, report = build_tables(config)
    provenance = {"generator_version": "seeded-v1", "code_hash": _code_hash(),
                  "python": platform.python_version(),
                  "numpy": importlib.metadata.version("numpy"),
                  "pydantic": importlib.metadata.version("pydantic")}
    return publish_dataset(root, tables, scenarios, config.model_dump(mode="json"), report,
                           provenance, output_format)


def export_parquet(root: Path, dataset_id: str) -> dict:
    original = validate_dataset(root, dataset_id)
    directory = _directory(root, dataset_id)
    tables = {name: json.loads((directory/entry["json"]).read_text())
              for name, entry in original["tables"].items()}
    scenarios = {path.stem: json.loads(path.read_text()) for path in (directory/"scenarios").glob("*.json")}
    config = json.loads((directory/"resolved_config.json").read_text())
    return publish_dataset(root, tables, scenarios, config, original["summary"],
                           original["identity"]["provenance"], "both")
