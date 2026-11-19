# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CANONICAL_APP_PATH=/opt/integration-task-manager

WORKDIR /build/miniapp
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && \
    pip install .

COPY --from=canonical / /opt/integration-task-manager
COPY --from=canonical docs/FAQ_CLIENT.md /app/help/FAQ_CLIENT.md
COPY --from=canonical docs/FAQ_TECHNICIAN.md /app/help/FAQ_TECHNICIAN.md
COPY --from=canonical docs/faq /app/help/faq
COPY migrations /app/migrations
COPY scripts /app/scripts
WORKDIR /app

RUN groupadd --gid 10001 miniapp && \
    useradd --uid 10001 --gid miniapp --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin miniapp
USER 10001:10001

CMD ["python", "-m", "dest_miniapp.runtime", "web"]

FROM runtime AS test
COPY --chown=miniapp:miniapp tests /app/tests
CMD ["python", "-m", "unittest", "discover", "-s", "/app/tests", "-v"]

FROM runtime AS production
