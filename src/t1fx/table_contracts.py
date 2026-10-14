"""Storage contracts shared by canonical JSON and the optional Arrow adapter."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    nullable: bool = False


@dataclass(frozen=True)
class TableContract:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    private: bool = False

    def normalize(self, rows: list[dict]) -> list[dict]:
        expected = {column.name for column in self.columns}
        normalized, seen = [], set()
        for row in rows:
            if set(row) != expected:
                raise ValueError(f"{self.name}: columns do not match the explicit schema")
            converted = {}
            for column in self.columns:
                value = row[column.name]
                if value is None:
                    if not column.nullable:
                        raise ValueError(f"{self.name}.{column.name} cannot be null")
                elif column.kind == "timestamp":
                    timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
                    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                        raise ValueError(f"{self.name}.{column.name} requires an aware timestamp")
                    value = timestamp.astimezone(UTC).isoformat(timespec="microseconds")
                elif column.kind == "int64":
                    if type(value) is not int or not -(2**63) <= value < 2**63:
                        raise ValueError(f"{self.name}.{column.name} requires a signed 64-bit integer")
                elif column.kind == "bool":
                    if type(value) is not bool:
                        raise ValueError(f"{self.name}.{column.name} requires a boolean")
                elif column.kind == "float64":
                    import math
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError(f"{self.name}.{column.name} requires a finite number")
                    value = float(value)
                elif column.kind == "strings":
                    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
                        raise ValueError(f"{self.name}.{column.name} requires a string list")
                    value = list(value)
                elif column.kind in ("string", "decimal_string"):
                    if isinstance(value, Decimal):
                        value = str(value)
                    if not isinstance(value, str):
                        raise ValueError(f"{self.name}.{column.name} requires a string")
                    if column.kind == "decimal_string":
                        try:
                            finite = Decimal(value).is_finite()
                        except InvalidOperation:
                            finite = False
                        if not finite:
                            raise ValueError(f"{self.name}.{column.name} requires a finite decimal")
                else:
                    raise ValueError(f"Unknown storage type: {column.kind}")
                converted[column.name] = value
            if converted["schema_version"] != "1.0":
                raise ValueError("Unsupported table schema version")
            if converted["split"] not in {"train", "tuning", "calibration", "test"}:
                raise ValueError("Unknown dataset partition")
            identity = tuple(converted[key] for key in self.key)
            if identity in seen:
                raise ValueError(f"{self.name}: duplicate primary key {identity}")
            seen.add(identity)
            normalized.append(converted)
        return sorted(normalized, key=lambda row: tuple(row[key] for key in self.key))

    def description(self) -> dict:
        return {"name": self.name, "key": list(self.key), "private": self.private,
                "columns": [{"name": c.name, "kind": c.kind, "nullable": c.nullable}
                            for c in self.columns]}


def columns(*definitions):
    return tuple(Column(*definition) for definition in definitions)


COMMON = columns(("schema_version", "string"), ("scenario_id", "string"), ("split", "string"))
BUCKET = columns(("cash_pool_id", "string"), ("currency", "string"))

TABLES = {
    "trades": TableContract("trades", COMMON + BUCKET + columns(
        ("trade_id", "string"), ("counterparty_id", "string"), ("security_id", "string"),
        ("market_profile_id", "string"), ("settlement_amount_minor", "int64"),
        ("executed_at", "timestamp"), ("available_at", "timestamp"),
        ("affirmation_cutoff_at", "timestamp"), ("settlement_open_at", "timestamp"),
        ("contractual_deadline_at", "timestamp"),
    ), ("trade_id",)),
    "fx_legs": TableContract("fx_legs", COMMON + BUCKET + columns(
        ("fx_leg_id", "string"), ("buy_amount_minor", "int64"),
        ("sell_currency", "string"), ("sell_amount_minor", "int64"),
        ("funding_source_id", "string"), ("booked_at", "timestamp"),
        ("available_at", "timestamp"), ("expected_credit_at", "timestamp"),
    ), ("fx_leg_id",)),
    "events": TableContract("events", COMMON + columns(
        ("event_id", "string"), ("event_type", "string"),
        ("event_time", "timestamp"), ("available_at", "timestamp"), ("sequence", "int64"),
        ("trade_id", "string", True), ("fx_leg_id", "string", True),
    ), ("event_id",)),
    "postings": TableContract("postings", COMMON + BUCKET + columns(
        ("posting_id", "string"), ("amount_minor", "int64"),
        ("effective_at", "timestamp"), ("available_at", "timestamp"),
        ("kind", "string"), ("source_ref", "string"),
    ), ("scenario_id", "posting_id")),
    "cash_balances": TableContract("cash_balances", COMMON + BUCKET + columns(
        ("opening_minor", "int64"), ("closing_minor", "int64"),
    ), ("scenario_id", "cash_pool_id", "currency")),
    "funding_sources": TableContract("funding_sources", COMMON + columns(
        ("source_id", "string"), ("currency", "string"), ("capacity_minor", "int64"),
    ), ("scenario_id", "source_id")),
    "snapshots": TableContract("snapshots", COMMON + BUCKET + columns(
        ("trade_id", "string"), ("as_of", "timestamp"), ("horizon_at", "timestamp"),
        ("remaining_obligation_minor", "int64"), ("instruction_state", "string"),
        ("ssi_status", "string"), ("observed_settled", "bool"),
        ("minutes_to_affirmation_cutoff", "float64"), ("minutes_to_settlement_deadline", "float64"),
        ("pool_usable_minor", "int64"), ("reference_policy_id", "string"),
    ), ("trade_id", "as_of")),
    "outcomes": TableContract("outcomes", COMMON + columns(
        ("trade_id", "string"), ("missed_contractual_deadline", "bool"),
        ("settled_at", "timestamp", True), ("outcome_known_at", "timestamp"),
        ("failure_reasons", "strings"), ("recovered_late", "bool"), ("censored", "bool"),
        ("late_cost", "decimal_string"), ("route_fee", "decimal_string"), ("currency", "string"),
    ), ("trade_id",)),
    "injections": TableContract("injections", COMMON + columns(
        ("trade_id", "string"), ("late_confirmation", "bool"), ("missed_fx_cutoff", "bool"),
        ("ssi_mismatch", "bool"), ("funding_stress", "bool"),
    ), ("trade_id",), private=True),
    "counterparty_truth": TableContract("counterparty_truth", COMMON + columns(
        ("counterparty_id", "string"), ("delay_multiplier", "float64"),
    ), ("scenario_id", "counterparty_id"), private=True),
}


def arrow_schema(contract: TableContract):
    try:
        import pyarrow as pa
    except ImportError as exc:
        raise RuntimeError("Parquet export requires PyArrow; install the project's [parquet] extra") from exc
    types = {"string": pa.string(), "decimal_string": pa.string(), "int64": pa.int64(),
             "float64": pa.float64(), "bool": pa.bool_(), "timestamp": pa.timestamp("us", tz="UTC"),
             "strings": pa.list_(pa.field("element", pa.string()))}
    return pa.schema([pa.field(c.name, types[c.kind], nullable=c.nullable) for c in contract.columns],
                     metadata={b"t1fx.schema_version": b"1.0", b"t1fx.table": contract.name.encode()})


def write_parquet(path: Path, contract: TableContract, rows: list[dict]) -> None:
    schema = arrow_schema(contract)
    import pyarrow as pa
    import pyarrow.parquet as pq
    normalized = contract.normalize(rows)
    timestamps = {column.name for column in contract.columns if column.kind == "timestamp"}
    converted = [{key: datetime.fromisoformat(value) if key in timestamps and value is not None else value
                  for key, value in row.items()} for row in normalized]
    table = pa.Table.from_pylist(converted, schema=schema)
    table.validate(full=True)
    pq.write_table(table, path, version="2.6", compression="zstd", row_group_size=65536,
                   use_dictionary=True, write_statistics=True)


def read_parquet(path: Path, contract: TableContract) -> list[dict]:
    schema = arrow_schema(contract)
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    if not table.schema.equals(schema, check_metadata=True):
        raise ValueError(f"{contract.name}: Parquet schema mismatch")
    table.validate(full=True)
    return contract.normalize(table.to_pylist())
