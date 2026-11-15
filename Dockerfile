FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=18520 \
    BILISUMMARY_BUNDLE_DIR=/app \
    BILISUMMARY_DATA_DIR=/data \
    BILISUMMARY_ENV_FILE=/app/.env.local

COPY . .

RUN mkdir -p /data/summary /data/ass /data/media /data/models

EXPOSE 18520
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.getenv('PORT', '18520'), timeout=3).read()" || exit 1

CMD ["sh", "-c", "python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-18520}"]
