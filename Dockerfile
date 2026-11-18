FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY kb ./kb

# Las credenciales y la URL de la base se inyectan por entorno, nunca en la imagen.
ENV WMS_LLM_PROVIDER=rule-based \
    WMS_EMBEDDING_PROVIDER=hashing \
    WMS_DB_URL=postgresql+psycopg://wms:wms@db:5432/wms

EXPOSE 8000

CMD ["sh", "-c", "python -m wms_copilot.cli bootstrap && uvicorn wms_copilot.api.main:app --host 0.0.0.0 --port 8000"]
