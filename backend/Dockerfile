FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC
WORKDIR /app
RUN groupadd --gid 10001 shadowchain \
    && useradd --uid 10001 --gid shadowchain --no-create-home shadowchain \
    && mkdir -p /app/artifacts/reports \
    && chown shadowchain:shadowchain /app/artifacts/reports
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt && python -m pip check
COPY --chown=shadowchain:shadowchain app ./app

FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt && python -m pip check
COPY --chown=shadowchain:shadowchain tests ./tests
USER shadowchain
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
USER shadowchain
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--ws-max-size", "4096", "--ws-max-queue", "4"]
