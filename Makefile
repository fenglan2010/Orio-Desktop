.PHONY: dev install test reset build worker

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt

install: $(VENV)/bin/python
	cd frontend && npm install

dev:
	bash scripts/dev.sh

test: $(VENV)/bin/python
	cd backend && ../$(VENV)/bin/pytest -q

reset: $(VENV)/bin/python
	bash scripts/reset_db.sh

build:
	cd frontend && npm install && npm run build

worker:
	bash scripts/worker.sh
