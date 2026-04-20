SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
INSTALL_STAMP := $(VENV)/.editable-installed
SEED_DEMO ?= 1

.PHONY: help venv install up status down token uninstall purge-local test build preview-check workflow-check grounded-pass-check

help:
	@printf "Targets:\n"
	@printf "  make up      Create/update the local env, start the localhost stack, and seed demo data by default.\n"
	@printf "  make status  Show the current localhost runtime status.\n"
	@printf "  make down    Stop the localhost runtime.\n"
	@printf "  make token   Print the managed localhost admin token and API key.\n"
	@printf "  make uninstall  Stop the localhost runtime and remove the managed Codex integration.\n"
	@printf "  make purge-local  Uninstall and also delete managed local config/data and docker volumes.\n"
	@printf "  make test    Run the test suite.\n"
	@printf "  make build   Build wheel and sdist artifacts.\n"
	@printf "  make preview-check  Run tests, build artifacts, and both smoke suites.\n"
	@printf "  make workflow-check  Run the repo-local HTTP e2e test plus the research harnesses.\n"
	@printf "  make grounded-pass-check  Run the 27-pass grounded research suite and write a markdown report.\n"
	@printf "\n"
	@printf "Options:\n"
	@printf "  SEED_DEMO=0  Skip demo content during make up.\n"

$(VENV_PYTHON):
	@$(PYTHON) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || \
		(printf "Research Registry requires Python 3.12+ for bootstrap. Set PYTHON=python3.12 or precreate .venv with a 3.12 interpreter.\n" >&2; exit 1)
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m ensurepip --upgrade
	$(VENV_PYTHON) -m pip install --upgrade pip

venv: $(VENV_PYTHON)

$(INSTALL_STAMP): $(VENV_PYTHON) pyproject.toml
	$(VENV_PYTHON) -m ensurepip --upgrade
	$(VENV_PYTHON) -m pip install -e ".[dev]"
	touch $(INSTALL_STAMP)

install: $(INSTALL_STAMP)

up: install
	$(VENV_PYTHON) -m research_registry.local_install
ifeq ($(SEED_DEMO),1)
	$(VENV_PYTHON) -m research_registry.seed_demo
	$(VENV_PYTHON) -m research_registry.seed_memory_retrieval
endif
	$(VENV_PYTHON) -m research_registry.local_status
	@printf "\nOpen http://127.0.0.1:8010\n"

status: install
	$(VENV_PYTHON) -m research_registry.local_status

down: install
	$(VENV_PYTHON) -m research_registry.local_stop

token: install
	$(VENV_PYTHON) -m research_registry.local_token

uninstall: install
	$(VENV_PYTHON) -m research_registry.local_uninstall

purge-local: install
	$(VENV_PYTHON) -m research_registry.local_uninstall --purge-data

test: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q

build: install
	PYTHONPATH=src $(VENV_PYTHON) -m build

preview-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q
	PYTHONPATH=src $(VENV_PYTHON) -m build
	RUN_LOCAL_INSTALL_SMOKE=1 PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_local_install_smoke.py
	RUN_SHARED_COMPOSE_SMOKE=1 PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_shared_compose_smoke.py

workflow-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_http_e2e.py
	RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS=$(CURDIR) PYTHONPATH=src $(VENV_PYTHON) -m research_registry.memory_retrieval_harness --all --reset --db-path .data/memory-retrieval-harness.sqlite3
	RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS=$(CURDIR) PYTHONPATH=src $(VENV_PYTHON) -m research_registry.domain_research_harness --all --reset --db-path .data/domain-research-harness.sqlite3

grounded-pass-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m research_registry.research_pass_runner --db-path .data/research-pass-runner.sqlite3 --reset --rounds 2 --markdown-out .data/research-pass-runner.md
