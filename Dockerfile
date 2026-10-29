# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && \
    pip install --requirement /tmp/requirements.txt

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid 10001 bot && \
    useradd --uid 10001 --gid bot --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin bot

COPY --from=dependencies /opt/venv /opt/venv
WORKDIR /app
COPY --chown=bot:bot main.py config.py ./
COPY --chown=bot:bot app ./app
COPY --chown=bot:bot content ./content

USER 10001:10001
CMD ["python", "main.py"]

FROM runtime AS test
COPY --chown=bot:bot tests ./tests
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM runtime AS production
