# Build context is the k8s-cluster-monitor/ directory (parent of this file),
# because the runtime image needs both backend/ and frontend/static/:
#   docker build -f backend/Dockerfile -t <registry>/k8s-cluster-monitor:vX.Y.Z .

FROM python:3.12-slim AS builder
WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY --from=builder /install /usr/local

WORKDIR /app
COPY backend/app /app/app
COPY frontend/static /app/frontend/static

ENV MONITOR_STATIC_DIR=/app/frontend/static \
    MONITOR_DB_PATH=/data/monitor.db \
    PYTHONUNBUFFERED=1

# /data (PVC) and /tmp (emptyDir, see deploy/k8s/08-deployment.yaml) are the
# only writable paths this process needs — everything else can run with a
# read-only root filesystem at the Deployment level.
RUN mkdir -p /data && chown appuser:appuser /data

USER 10001
EXPOSE 8080

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
