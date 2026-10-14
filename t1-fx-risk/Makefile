PYTHON ?= .venv/bin/python
DATASET_FORMAT ?= auto

.PHONY: setup test demo smoke train serve fixture check

setup:
	uv sync --extra parquet --extra ml

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m t1fx.cli demo

smoke:
	$(PYTHON) -m t1fx.cli generate --config configs/generation/smoke.json --format $(DATASET_FORMAT)

train:
	$(PYTHON) -m t1fx.cli train --dataset $(DATASET)

serve:
	$(PYTHON) -m t1fx.cli serve

fixture:
	$(PYTHON) -m t1fx.cli write-fixture --output configs/scenarios/three-trades.json

check: test
	$(PYTHON) -m compileall -q src
