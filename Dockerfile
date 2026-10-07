# One image for every Python service: the API, the crawler and migrations.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY packages/core packages/core
COPY services/crawler services/crawler
COPY services/api services/api
RUN pip install ./packages/core ./services/crawler ./services/api

COPY registry registry

RUN useradd --system --no-create-home addex
USER addex
