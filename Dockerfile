FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY alembic/ alembic/
COPY alembic.ini .
COPY src/ src/
RUN uv run alembic upgrade head
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "odds_engine.main:app", "--host", "0.0.0.0", "--port", "8000"]
