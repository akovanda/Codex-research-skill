SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
INSTALL_STAMP := $(VENV)/.editable-installed
SEED_DEMO ?= 1

.PHONY: help venv install up status down test build

help:
	@printf "Targets:\n"
	@printf "  make up      Create/update the local env, start the localhost stack, and seed demo data by default.\n"
	@printf "  make status  Show the current localhost runtime status.\n"
	@printf "  make down    Stop the localhost runtime.\n"
	@printf "  make test    Run the test suite.\n"
	@printf "  make build   Build wheel and sdist artifacts.\n"
	@printf "\n"
	@printf "Options:\n"
	@printf "  SEED_DEMO=0  Skip demo content during make up.\n"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip

venv: $(VENV_PYTHON)

$(INSTALL_STAMP): $(VENV_PYTHON) pyproject.toml
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

test: install
	PYTHONPATH=src $(VENV_PYTHON) -m pytest -q

build: install
	PYTHONPATH=src $(VENV_PYTHON) -m build
