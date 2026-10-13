PYTHON ?= .venv/bin/python

.PHONY: setup test demo serve fixture check

setup:
	uv sync

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m t1fx.cli demo

serve:
	$(PYTHON) -m t1fx.cli serve

fixture:
	$(PYTHON) -m t1fx.cli write-fixture --output configs/scenarios/three-trades.json

check: test
	$(PYTHON) -m compileall -q src
