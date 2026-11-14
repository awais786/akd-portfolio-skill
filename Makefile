# AKD App - developer/user commands. Run `make` (or `make help`) to list them.
# Works with plain Python + pip. No uv, Node, Docker or JVM needed.

PYTHON  ?= python3
VENV    ?= .venv
BIN      = $(VENV)/bin
PY       = $(BIN)/python
STAMP    = $(VENV)/.installed

.DEFAULT_GOAL := help

.PHONY: help check-python venv install portfolio test test-live lint fmt check clean distclean

help: ## Show this help
	@echo "AKD portfolio - available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "} {printf "  make %-14s %s\n", $$1, $$2}'
	@echo ""
	@echo "First time:  make install, then export AKD_USER_ID and AKD_PASSWORD, then make portfolio"

check-python: ## Verify Python >= 3.11 is available
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || \
	  { echo "ERROR: Python 3.11+ required (found: $$($(PYTHON) --version 2>&1)). Try: make install PYTHON=python3.12"; exit 1; }

$(PY): | check-python
	$(PYTHON) -m venv $(VENV)

venv: $(PY) ## Create the virtualenv (.venv)

# Re-installs automatically when the requirements or pyproject change.
$(STAMP): $(PY) requirements.txt requirements-dev.txt pyproject.toml
	@$(PY) -m pip --version >/dev/null 2>&1 || $(PY) -m ensurepip --upgrade >/dev/null  # uv-made venvs ship without pip
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements-dev.txt
	$(PY) -m pip install --quiet -e . --no-deps
	@touch $(STAMP)

install: $(STAMP) ## Create .venv and install everything (runtime + dev)
	@echo "Installed. Next: export AKD_USER_ID=... AKD_PASSWORD=... then make portfolio"

portfolio: $(STAMP) ## Fetch the portfolio as JSON (needs AKD_USER_ID and AKD_PASSWORD in the environment). ONE login attempt
	@$(PY) -m akd portfolio

test: $(STAMP) ## Run the test suite (offline, no real credentials)
	$(PY) -m pytest -q

test-live: $(STAMP) ## Log into the REAL AKD (needs AKD_ACCOUNT and AKD_PASSWORD env vars)
	@test -n "$$AKD_ACCOUNT" -a -n "$$AKD_PASSWORD" || { echo "Set AKD_ACCOUNT and AKD_PASSWORD first."; exit 1; }
	$(PY) -m pytest -m live tests/test_daemon.py::test_live_login_round_trip -v

lint: $(STAMP) ## Lint with ruff
	$(BIN)/ruff check src tests

fmt: $(STAMP) ## Auto-fix lint issues (imports, upgrades)
	$(BIN)/ruff check --fix src tests

check: lint test ## Lint + tests (run before committing)

clean: ## Remove caches and build artifacts (keeps .venv and data)
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name '*.egg-info' \) \
	  -not -path './$(VENV)/*' -prune -exec rm -rf {} +

distclean: clean ## clean + remove .venv (keeps data and .env)
	rm -rf $(VENV)
