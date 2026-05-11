# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080

RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --home /app app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app main.py ./
COPY --chown=app:app app ./app
COPY --chown=app:app static ./static
COPY --chown=app:app mcat_data.xlsx evidence_data.xlsx ./

USER app

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
