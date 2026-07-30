SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
INSTALL_STAMP := $(VENV)/.editable-installed
SEED_DEMO ?= 1
BACKUP_OUTPUT ?= $(CURDIR)/research-registry.backup.sqlite3
OPERATOR_EVIDENCE ?=

.PHONY: help venv install init up mcp serve backup shared-up status doctor repair down token uninstall purge-local test build preview-check workflow-check grounded-pass-check rr2-contract-check rr2-migration-check rr2-mcp-check rr2-retrieval-eval rr2-security-check rr2-package-check rr2-rehearsal-check rr2-regression-check rr2-release-artifacts rr2-release-check rr2-alpha-check rr2-beta-check rr2-stable-check

help:
	@printf "Targets:\n"
	@printf "  make init    Create/update the local env and initialize personal SQLite storage.\n"
	@printf "  make up      Alias for the no-daemon personal init path.\n"
	@printf "  make mcp     Run tokenless local STDIO MCP in the foreground.\n"
	@printf "  make serve   Run the optional review server (requires RESEARCH_REGISTRY_ADMIN_TOKEN).\n"
	@printf "  make backup  Create a verified personal backup at BACKUP_OUTPUT.\n"
	@printf "  make shared-up  Start the retained Docker/Postgres localhost stack.\n"
	@printf "  make status  Show the retained shared localhost runtime status.\n"
	@printf "  make doctor  Check personal SQLite, blobs, migrations, MCP, and backup health.\n"
	@printf "  make repair  Repair managed Codex MCP config and skill links.\n"
	@printf "  make down    Stop the localhost runtime.\n"
	@printf "  make token   Print the managed localhost admin token and API key.\n"
	@printf "  make uninstall  Stop the localhost runtime and remove the managed Codex integration.\n"
	@printf "  make purge-local  Uninstall and also delete managed local config/data and docker volumes.\n"
	@printf "  make test    Run the test suite.\n"
	@printf "  make build   Build wheel and sdist artifacts.\n"
	@printf "  make preview-check  Run tests, build artifacts, and both smoke suites.\n"
	@printf "  make rr2-contract-check  Run v1/v2 contract and schema snapshots.\n"
	@printf "  make rr2-migration-check  Run dialect-aware migration tests and the read-only plan.\n"
	@printf "  make rr2-mcp-check  Run high-level, Deep Research, and compatibility MCP checks.\n"
	@printf "  make rr2-retrieval-eval  Run fixed synthetic retrieval and four-mode comparison metrics.\n"
	@printf "  make rr2-security-check  Run SSRF, fuzz, privacy, log, ingestion, and atomicity checks.\n"
	@printf "  make rr2-package-check  Build and smoke wheel/sdist with clean local homes.\n"
	@printf "  make rr2-rehearsal-check  Rehearse fresh SQLite, upgrade, backup, restore, and rollback.\n"
	@printf "  make rr2-release-check  Expose and compose every automated RR2 release constituent.\n"
	@printf "  make rr2-alpha-check  Require the fixed alpha gate.\n"
	@printf "  make rr2-beta-check  Require beta plus OPERATOR_EVIDENCE JSON.\n"
	@printf "  make rr2-stable-check  Require stable plus OPERATOR_EVIDENCE JSON.\n"
	@printf "  make workflow-check  Run the repo-local HTTP e2e test plus the research harnesses.\n"
	@printf "  make grounded-pass-check  Run the 27-pass grounded research suite and write a markdown report.\n"
	@printf "\n"
	@printf "Options:\n"
	@printf "  BACKUP_OUTPUT=/safe/path/registry.sqlite3  Select the personal backup path.\n"
	@printf "  SEED_DEMO=0  Skip demo content during make shared-up.\n"

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

init: install
	$(VENV_PYTHON) -m research_registry init

up: init

mcp: init
	$(VENV_PYTHON) -m research_registry mcp

serve: init
	@test -n "$$RESEARCH_REGISTRY_ADMIN_TOKEN" || \
		(printf "Set RESEARCH_REGISTRY_ADMIN_TOKEN in the environment before make serve.\n" >&2; exit 1)
	$(VENV_PYTHON) -m research_registry serve

backup: init
	$(VENV_PYTHON) -m research_registry backup --output "$(BACKUP_OUTPUT)"

shared-up: install
	$(VENV_PYTHON) -m research_registry up --build-local-image --image research-registry-local:latest
ifeq ($(SEED_DEMO),1)
	$(VENV_PYTHON) -m research_registry.seed_demo
	$(VENV_PYTHON) -m research_registry.seed_memory_retrieval
endif
	$(VENV_PYTHON) -m research_registry.local_status
	@printf "\nOpen http://127.0.0.1:8010\n"

status: install
	$(VENV_PYTHON) -m research_registry status

doctor: install
	$(VENV_PYTHON) -m research_registry doctor

repair: install
	$(VENV_PYTHON) -m research_registry repair

down: install
	$(VENV_PYTHON) -m research_registry down

token: install
	$(VENV_PYTHON) -m research_registry token

uninstall: install
	$(VENV_PYTHON) -m research_registry uninstall

purge-local: install
	$(VENV_PYTHON) -m research_registry uninstall --purge-data

test: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q

build: install
	PYTHONPATH=src $(VENV_PYTHON) -m build

preview-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q
	PYTHONPATH=src $(VENV_PYTHON) -m build
	RUN_LOCAL_INSTALL_SMOKE=1 PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_local_install_smoke.py
	RUN_SHARED_COMPOSE_SMOKE=1 PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_shared_compose_smoke.py

rr2-contract-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_v2_contracts.py tests/test_contract_snapshots.py tests/test_docs_contract.py

rr2-migration-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_v2_migration.py tests/test_migrations.py tests/test_postgres_smoke.py
	PYTHONPATH=src $(VENV_PYTHON) -m research_registry migrate --plan --json

rr2-mcp-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_mcp_v2_read.py tests/test_mcp_v2_write.py tests/test_deep_research_mcp.py tests/test_plugin_v2.py tests/test_contract_snapshots.py

rr2-retrieval-eval: install
	PYTHONPATH=src $(VENV_PYTHON) -m research_registry eval-retrieval --corpus evals/retrieval/synthetic.json --release-level stable
	PYTHONPATH=src $(VENV_PYTHON) -m research_registry eval-comparative --corpus evals/comparative/synthetic.json

rr2-security-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/security tests/test_external_ingest_v2.py tests/test_git_evidence.py tests/test_blob_store.py tests/test_reanchor.py tests/test_v2_deposit.py tests/test_web_v2.py

rr2-package-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m build
	RUN_LOCAL_INSTALL_SMOKE=1 PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_local_install_smoke.py

rr2-rehearsal-check: install
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_rehearsal.py

rr2-regression-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q

rr2-release-artifacts: install
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_release_artifacts.py

rr2-release-check: install
	$(MAKE) rr2-contract-check
	$(MAKE) rr2-migration-check
	$(MAKE) rr2-mcp-check
	$(MAKE) rr2-retrieval-eval
	$(MAKE) rr2-security-check
	$(MAKE) rr2-package-check
	$(MAKE) rr2-rehearsal-check
	$(MAKE) rr2-regression-check
	$(MAKE) rr2-release-artifacts
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_release_status.py --automated $(if $(OPERATOR_EVIDENCE),--operator-evidence "$(OPERATOR_EVIDENCE)",)

rr2-alpha-check: rr2-release-check
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_release_status.py --automated --require alpha $(if $(OPERATOR_EVIDENCE),--operator-evidence "$(OPERATOR_EVIDENCE)",)

rr2-beta-check: rr2-release-check
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_release_status.py --automated --require beta $(if $(OPERATOR_EVIDENCE),--operator-evidence "$(OPERATOR_EVIDENCE)",)

rr2-stable-check: rr2-release-check
	PYTHONPATH=src $(VENV_PYTHON) scripts/rr2_release_status.py --automated --require stable $(if $(OPERATOR_EVIDENCE),--operator-evidence "$(OPERATOR_EVIDENCE)",)

workflow-check: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q tests/test_http_e2e.py
	RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS=$(CURDIR) PYTHONPATH=src $(VENV_PYTHON) -m research_registry.memory_retrieval_harness --all --reset --db-path .data/memory-retrieval-harness.sqlite3
	RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS=$(CURDIR) PYTHONPATH=src $(VENV_PYTHON) -m research_registry.domain_research_harness --all --reset --db-path .data/domain-research-harness.sqlite3

grounded-pass-check: install
	RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 PYTHONPATH=src $(VENV_PYTHON) -m research_registry.research_pass_runner --db-path .data/research-pass-runner.sqlite3 --reset --rounds 2 --markdown-out .data/research-pass-runner.md
