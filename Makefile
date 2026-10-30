.PHONY: setup env dev dev-worker dev-socketio test lint format typecheck migrate migration docker-up docker-down devbox-image clean

# Setup local environment, dependencies, and database migrations
setup:
	uv sync --all-extras
	@$(MAKE) env
	uv run python scripts/setup_db.py

# Create .env with generated local secrets (never overwrites an existing file)
env:
	@uv run python scripts/generate_env.py

# Seed sample development data into the database
seed:
	uv run python scripts/seed.py

# Start API server with hot-reload (override with: make dev PORT=8010)
PORT ?= 8001
dev:
	uv run uvicorn src.bootstrap.api:app --reload --host 0.0.0.0 --port $(PORT)

# Start background event worker pool
dev-worker:
	uv run python -m src.bootstrap.workers

# Start standalone Socket.IO realtime server
dev-socketio:
	uv run uvicorn src.bootstrap.socketio:sio_app --reload --host 0.0.0.0 --port 8002

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

# Build the local devbox image (DEVBOX_PROVIDER=local)
devbox-image:
	docker build -f docker/Dockerfile.devbox -t congruence/devbox:dev .

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
