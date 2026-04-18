.PHONY: lint-all test-all

lint-all:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy

test-all:
	uv run pytest --cov --cov-report=term-missing
