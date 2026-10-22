# Deployment container for ZeroTrace (FastAPI + SQLite).
# Attach a persistent volume at /data and set ZT_DATABASE_PATH=/data/zerotrace.sqlite3.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY app ./app

# Railway (and similar hosts) supplies PORT at runtime.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --log-level warning"]
