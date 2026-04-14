.PHONY: lint-all test-all

lint-all:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy --strict contracts/python services

test-all:
	uv run pytest services --cov --cov-report=term-missing
