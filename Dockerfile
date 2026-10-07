FROM python:3.12-slim-bookworm

# Install system dependencies: git, zsh, bash, curl, nodejs, npm
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    zsh \
    bash \
    curl \
    ca-certificates \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy pyproject and install dependencies
COPY pyproject.toml .
RUN uv venv && uv sync --no-dev

COPY . .

ENV PORT=8000
ENV ENVIRONMENT=production
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
