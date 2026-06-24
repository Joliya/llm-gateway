FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[postgres,redis]"

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
# Entrypoint runs `alembic upgrade head`, then the CMD. Set GW_AUTO_CREATE_TABLES=false
# (see compose) so migrations own the schema instead of create_all.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
