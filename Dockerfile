FROM python:3.11-slim AS base

# System deps for USB/HID access (StreamDeck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libusb-1.0-0 \
        libhidapi-libusb0 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first (cache-friendly)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY main.py helpers.py ./
COPY controllers/ controllers/
COPY dui/ dui/

# Install the project itself
RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "main.py"]
