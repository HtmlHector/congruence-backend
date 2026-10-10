.PHONY: setup dev dev-worker dev-socketio test lint format typecheck migrate migration docker-up docker-down clean

# Setup local environment, dependencies, and database migrations
setup:
	uv sync --all-extras
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example"; fi
	uv run python scripts/setup_db.py

# Start API server with hot-reload
dev:
	uv run uvicorn src.bootstrap.api:app --reload --host 0.0.0.0 --port 8000

# Start background event worker pool
dev-worker:
	uv run python -m src.bootstrap.workers

# Start standalone Socket.IO realtime server
dev-socketio:
	uv run uvicorn src.bootstrap.socketio:sio_app --reload --host 0.0.0.0 --port 8001

# Run test suite
test:
	uv run pytest -v

# Run code linter
lint:
	uv run ruff check src tests

# Format code
format:
	uv run ruff format src tests

# Typecheck with mypy
typecheck:
	uv run mypy src tests

# Apply database schema migrations
migrate:
	uv run alembic upgrade head

# Generate a new migration revision
migration:
	@if [ -z "$(name)" ]; then echo "Usage: make migration name=add_feature_x"; exit 1; fi
	uv run alembic revision --autogenerate -m "$(name)"

# Start local infrastructure dependencies
docker-up:
	docker compose up -d postgres redis rabbitmq minio

# Stop local infrastructure
docker-down:
	docker compose down

# Clean python cache files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
