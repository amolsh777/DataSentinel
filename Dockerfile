# ── DataSentinel backend ────────────────────────────────────────────────────
# Build: docker build -t datasentinel-backend .
# Run:   docker run -p 8000:8000 -v $(pwd)/data:/app/data datasentinel-backend

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DS_APP_ENV=production \
    DS_DATABASE_URL=sqlite:////app/data/datasentinel.db \
    DS_STORAGE_DIR=/app/data/storage

WORKDIR /app

# Install system deps needed by pyarrow / scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/
COPY data/sample/ ./data/sample/

# Create data directories
RUN mkdir -p /app/data/storage /app/reports

WORKDIR /app/backend

# Run Alembic migrations then start uvicorn
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.asgi:app --host 0.0.0.0 --port 8000 --workers 2"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
