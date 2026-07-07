# Stage 1: Build environment using uv
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app

# Set environment variables to ensure uv works correctly
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy dependency definitions
COPY pyproject.toml uv.lock guide.md ./

# Sync dependencies (without installing the project itself)
RUN uv sync --frozen --no-install-project --no-dev

# Copy source code
COPY app/ ./app/

# Sync the project (if needed)
RUN uv sync --frozen --no-dev

# Stage 2: Runtime environment
FROM python:3.11-slim-bookworm

WORKDIR /app

# Copy the virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv
COPY app/ ./app/

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Default command (API server)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
