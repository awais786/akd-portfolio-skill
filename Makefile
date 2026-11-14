# AKD App - developer/user commands. Run `make` (or `make help`) to list them.
# Works with plain Python + pip. No uv, Node, Docker or JVM needed.

PYTHON  ?= python3
VENV    ?= .venv
BIN      = $(VENV)/bin
PY       = $(BIN)/python
PORT    ?= 8080
HOST    ?= 127.0.0.1
DATA    ?= data
STAMP    = $(VENV)/.installed

.DEFAULT_GOAL := help
.PHONY: help check-python venv install run dev test test-live lint fmt check health open status \
        logout reset-data clean distclean

help: ## Show this help
	@echo "AKD App - available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  make %-12s %s\n", $$1, $$2}'
	@echo ""
	@echo "First time:  make install && make run     (then open http://$(HOST):$(PORT))"
	@echo "Override:    make run PORT=9000 DATA=~/.akd"

check-python: ## Verify Python >= 3.11 is available
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || \
	  { echo "ERROR: Python 3.11+ required (found: $$($(PYTHON) --version 2>&1)). Try: make install PYTHON=python3.12"; exit 1; }

$(PY): | check-python
	$(PYTHON) -m venv $(VENV)

venv: $(PY) ## Create the virtualenv (.venv)

# Re-installs automatically when the requirements or pyproject change.
$(STAMP): $(PY) requirements.txt requirements-dev.txt pyproject.toml
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements-dev.txt
	$(PY) -m pip install --quiet -e . --no-deps
	@touch $(STAMP)

install: $(STAMP) ## Create .venv and install everything (runtime + dev)
	@echo "Installed. Next: make run"

run: $(STAMP) ## Start the app (http://HOST:PORT)
	AKD_HOST=$(HOST) AKD_PORT=$(PORT) AKD_DATA_DIR=$(DATA) $(PY) -m akd

dev: $(STAMP) ## Start with auto-reload on code changes
	AKD_DATA_DIR=$(DATA) $(BIN)/uvicorn akd.api:app --host $(HOST) --port $(PORT) --reload --reload-dir src

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

health: ## Ping a running server
	@curl -fsS --max-time 5 http://$(HOST):$(PORT)/api/health && echo || { echo "Not running on $(HOST):$(PORT)"; exit 1; }

status: ## Show connection status from a running server
	@curl -fsS --max-time 5 http://$(HOST):$(PORT)/api/akd/status | $(PYTHON) -m json.tool

open: ## Open the UI in your browser
	@$(PYTHON) -m webbrowser http://$(HOST):$(PORT) >/dev/null

logout: ## Forget the saved AKD login (deletes encrypted credentials; keeps captured data)
	rm -f $(DATA)/akd_creds.enc $(DATA)/akd_creds.key
	@echo "Saved login removed."

reset-data: ## DELETE all captured data and saved login in DATA (asks first)
	@printf "Delete everything in '$(DATA)'? [y/N] " && read a && [ "$$a" = y ] && rm -rf $(DATA) && echo "Removed $(DATA)" || echo "Cancelled"

clean: ## Remove caches and build artifacts (keeps .venv and data)
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name '*.egg-info' \) \
	  -not -path './$(VENV)/*' -prune -exec rm -rf {} +

distclean: clean ## clean + remove .venv (keeps data)
	rm -rf $(VENV)
