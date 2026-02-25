FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY assets/ assets/
COPY src/ src/
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "python", "-m", "gemini_watermark_bot"]
