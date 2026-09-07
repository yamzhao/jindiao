FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app

# Build from the project root. Do not copy .env, artifacts, model SDKs or agents.
COPY deploy/bff/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY src/jindiao/__init__.py /app/src/jindiao/__init__.py
COPY src/jindiao/bff /app/src/jindiao/bff
COPY src/jindiao/contracts /app/src/jindiao/contracts
COPY src/jindiao/security /app/src/jindiao/security

USER 10001:10001
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8082/healthz', timeout=2); raise SystemExit(0 if r.status == 200 else 1)"]

# The host publication MUST be loopback-only until HTTPS deployment is approved.
CMD ["python", "-m", "uvicorn", "jindiao.bff.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8082", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
