FROM python:3.12-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic ./alembic
COPY alembic.ini ./
COPY templates ./templates
COPY static ./static

EXPOSE 8000
