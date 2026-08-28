FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    GITHUB_PRIVATE_KEY_PATH=/run/secrets/private-key.pem

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/

RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --gid 1001 --disabled-password appuser && \
    mkdir -p /run/secrets && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)" || exit 1

CMD ["python", "-m", "app.main"]
