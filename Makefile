PYTHON ?= .venv/bin/python
DATASET_FORMAT ?= auto

.PHONY: setup test demo smoke serve fixture check

setup:
	uv sync --extra parquet

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m t1fx.cli demo

smoke:
	$(PYTHON) -m t1fx.cli generate --config configs/generation/smoke.json --format $(DATASET_FORMAT)

serve:
	$(PYTHON) -m t1fx.cli serve

fixture:
	$(PYTHON) -m t1fx.cli write-fixture --output configs/scenarios/three-trades.json

check: test
	$(PYTHON) -m compileall -q src
