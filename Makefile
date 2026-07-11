.PHONY: lint-all test-all pre-push dashboard-lint dashboard-test review-profitability

lint-all:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	$(MAKE) dashboard-lint

test-all:
	uv run pytest --cov --cov-report=term-missing
	$(MAKE) dashboard-test

pre-push:
	$(MAKE) lint-all
	$(MAKE) test-all

dashboard-lint:
	cd dashboard && npm run lint && npm run typecheck

dashboard-test:
	cd dashboard && npm test

review-profitability:
	uv run python scripts/check-profitability-review.py
