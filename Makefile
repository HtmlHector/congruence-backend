.PHONY: help install dev test lint migrate init-db seed-db up down

help:
	@echo "Available commands:"
	@echo "  make install  - Install dependencies via uv"
	@echo "  make dev      - Start FastAPI server in reload mode"
	@echo "  make test     - Run pytest suite"
	@echo "  make lint     - Run ruff linter and formatter check"
	@echo "  make migrate  - Run alembic database migrations"
	@echo "  make init-db  - Initialize database schema"
	@echo "  make seed-db  - Initialize and seed sample development data"
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

init-db:
	uv run python scripts/init_db.py

seed-db:
	uv run python scripts/init_db.py --seed

up:
	docker compose up -d

down:
	docker compose down
