# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app
WORKDIR /app

FROM base AS deps
COPY pyproject.toml ./
# install third-party dependencies only (the stub package is removed so it cannot shadow /app/app)
RUN mkdir app && touch app/__init__.py && pip install --upgrade pip && pip install . \
    && pip uninstall -y aiecc-backend && rm -rf app

FROM base AS runtime
RUN groupadd -r aiecc && useradd -r -g aiecc -d /app aiecc
COPY --from=deps /usr/local /usr/local
COPY --chown=aiecc:aiecc alembic.ini ./
COPY --chown=aiecc:aiecc migrations ./migrations
COPY --chown=aiecc:aiecc app ./app
USER aiecc
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8100/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--timeout-graceful-shutdown", "20"]
