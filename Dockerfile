FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock /app/
COPY app /app/app

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "app.main", "--role", "api", "--port", "8000"]
