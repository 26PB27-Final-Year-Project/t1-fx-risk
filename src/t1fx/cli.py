"""CLI entry points; importing the API never launches a training or simulation job."""

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from t1fx.domain import Scenario
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore, canonical_bytes


def usd_minor(value: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount*100 != (amount*100).to_integral_value():
            raise ValueError
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("Use a nonnegative USD amount with at most two decimals") from None
    return int(amount*100)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="t1fx", description="T+1 FX settlement research engine")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Replay the three-trade fixture with four funding policies")
    demo.add_argument("--budget", type=usd_minor, default=3_000_000, help="USD amount; default 30000")
    simulation = sub.add_parser("simulate", help="Compare policies on a validated scenario JSON")
    simulation.add_argument("--scenario", type=Path, required=True)
    simulation.add_argument("--budget", type=usd_minor, default=3_000_000)
    fixture = sub.add_parser("write-fixture", help="Export the editable three-trade scenario")
    fixture.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate", help="Verify every artifact checksum for a run")
    validate.add_argument("--run", required=True)
    generate = sub.add_parser("generate", help="Build a seeded, partitioned research dataset")
    generate.add_argument("--config", type=Path, default=Path("configs/generation/smoke.json"))
    generate.add_argument("--seed", type=int)
    generate.add_argument("--trades", type=int)
    generate.add_argument("--format", choices=("auto", "json", "both"), default="auto")
    dataset_validate = sub.add_parser("validate-dataset", help="Validate a released dataset")
    dataset_validate.add_argument("--dataset", required=True)
    parquet = sub.add_parser("export-parquet", help="Publish a Parquet copy of a JSON dataset")
    parquet.add_argument("--dataset", required=True)
    serve = sub.add_parser("serve", help="Serve the read-only API and interactive OpenAPI docs")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    store = RunStore(args.artifacts)
    if args.command in ("demo", "simulate"):
        scenario = three_trade_scenario() if args.command == "demo" else Scenario.model_validate_json(
            args.scenario.read_text(),
        )
        report = run_comparison(scenario, args.budget, store)
        args.artifacts.mkdir(parents=True, exist_ok=True)
        (args.artifacts/"demo-summary.json").write_bytes(canonical_bytes(report))
        print(json.dumps(report, indent=2))
    elif args.command == "write-fixture":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(three_trade_scenario().model_dump(mode="json")))
        print(args.output)
    elif args.command == "validate":
        manifest = store.validate(args.run)
        print(f"Validated {args.run}: {len(manifest['files'])} artifacts")
    elif args.command in ("generate", "validate-dataset", "export-parquet"):
        from t1fx.datasets import export_parquet, generate_release, validate_dataset
        from t1fx.generation import GenerationConfig, load_config

        try:
            if args.command == "generate":
                config_data = load_config(args.config).model_dump()
                if args.seed is not None:
                    config_data["seed"] = args.seed
                if args.trades is not None:
                    config_data["trade_count"] = args.trades
                config = GenerationConfig.model_validate(config_data)
                manifest = generate_release(config, args.artifacts, args.format)
            elif args.command == "validate-dataset":
                manifest = validate_dataset(args.artifacts, args.dataset)
            else:
                manifest = export_parquet(args.artifacts, args.dataset)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            parser.error(str(exc))
        print(json.dumps({
            "dataset_id": manifest["dataset_id"],
            "directory": str(args.artifacts/"datasets"/manifest["dataset_id"]),
            "formats": manifest["identity"]["formats"],
            "parquet_status": manifest["parquet_status"],
            "summary": manifest["summary"],
        }, indent=2))
    elif args.command == "serve":
        import uvicorn

        from t1fx.api import create_app

        uvicorn.run(create_app(args.artifacts), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
