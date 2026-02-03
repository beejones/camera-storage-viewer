.PHONY: venv install test test-collect

VENV ?= .venv
PY := $(VENV)/bin/python

venv:
	@test -x $(PY) || python3 -m venv $(VENV)

install: venv
	$(PY) -m pip install -r requirements.txt

test: install
	$(PY) -m pytest -q

test-collect: install
	$(PY) -m pytest -q --collect-only
