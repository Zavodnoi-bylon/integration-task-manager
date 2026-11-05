FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.lock \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin activity
COPY app.py lead_app.py sync.py ./
COPY activity ./activity
COPY lead_static ./lead_static
COPY --from=frontend /build/dist ./dist
# Demo fixtures are deliberately absent from the production image.
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

