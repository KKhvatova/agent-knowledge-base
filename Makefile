.PHONY: help setup up down reset sync release eval serve observe test lint \
        demo-update demo-incident demo-rollback token pdf

help:            ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

setup:           ## Install dependencies, start Postgres, sync sources, create the first release
	uv sync
	docker compose up -d --wait postgres
	uv run kb reset --yes
	uv run kb sync --reconcile
	uv run kb release

up:              ## Start Postgres
	docker compose up -d --wait postgres

down:            ## Stop containers (data is kept in a Docker volume)
	docker compose down

reset:           ## Wipe the knowledge store and recreate the sources working copy
	uv run kb reset --yes

sync:            ## Sync all sources (events + polling + reconciliation)
	uv run kb sync --reconcile

release:         ## Build a candidate release, run evals, promote if they pass
	uv run kb release

eval:            ## Run the eval suite on the active release
	uv run kb eval

serve:           ## Run the MCP server on http://127.0.0.1:8765/mcp
	uv run kb serve

observe:         ## Run the observer agent once
	uv run kb observe

test:            ## Run the test suite (needs Postgres)
	uv run pytest -q

lint:            ## Lint and format check
	uv run ruff check . && uv run ruff format --check .

token:           ## Print a token: make token EMAIL=liam.chen@flipp.example
	@uv run kb token $(EMAIL)

demo-update:     ## Owner edits a policy -> sync -> gated release -> new answer
	@$(MAKE) --no-print-directory reset sync release > /dev/null
	uv run python scripts/demo.py edit-policy
	uv run kb sync
	uv run kb release
	uv run kb repo diff 1 2

demo-incident:   ## Corrupted page -> gate rejects -> observer quarantines and opens ticket
	@$(MAKE) --no-print-directory reset sync release > /dev/null
	uv run python scripts/demo.py corrupt-handbook
	uv run kb sync
	-uv run kb release
	uv run kb observe
	uv run kb release

demo-rollback:   ## Forced bad release -> observer detects regression -> rollback + verify
	@$(MAKE) --no-print-directory reset sync release > /dev/null
	uv run python scripts/demo.py corrupt-handbook
	uv run kb sync
	uv run kb release --force
	uv run kb observe
	uv run kb repo log
