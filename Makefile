.PHONY: help install dev test lint migrate up down

help:
	@echo "Available commands:"
	@echo "  make install  - Install dependencies via uv"
	@echo "  make dev      - Start FastAPI server in reload mode"
	@echo "  make test     - Run pytest suite"
	@echo "  make lint     - Run ruff linter and formatter check"
	@echo "  make migrate  - Run alembic database migrations"
	@echo "  make up       - Start Postgres and Redis via Docker Compose"
	@echo "  make down     - Stop Docker Compose services"

install:
	uv sync

dev:
	uv run uvicorn src.main:app --reload --port 8000

test:
	uv run pytest -v

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

up:
	docker compose up -d

down:
	docker compose down
