ARG JINDIAO_BASE_IMAGE=python:3.11-slim-bookworm
FROM ${JINDIAO_BASE_IMAGE} AS runtime
USER root
ARG DEBIAN_MIRROR=deb.debian.org
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG JINDIAO_INSTALL_DEPS=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

RUN if [ "$JINDIAO_INSTALL_DEPS" = "true" ]; then \
      sed -i "s|http://deb.debian.org|https://${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
      && apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
      && apt-get install --yes --no-install-recommends git \
      && rm -rf /var/lib/apt/lists/* \
      && groupadd --system jindiao \
      && useradd --system --gid jindiao --create-home jindiao; \
    else \
      id jindiao >/dev/null; \
    fi

WORKDIR /app
COPY requirements.txt pyproject.toml uv.lock README.md ./

ARG UV_HTTP_TIMEOUT=120
ARG UV_CONCURRENT_DOWNLOADS=4
RUN if [ "$JINDIAO_INSTALL_DEPS" = "true" ]; then \
      python -m pip install --no-cache-dir --index-url "$PYPI_INDEX_URL" uv==0.12.10 \
      && uv export --frozen --extra agentarts --group dev --no-emit-project \
          --output-file /tmp/jindiao-requirements.txt --quiet \
      && uv pip install --system --no-deps --default-index "$PYPI_INDEX_URL" -r /tmp/jindiao-requirements.txt; \
    fi

COPY src ./src
COPY config ./config
COPY mock_data ./mock_data
COPY skills ./skills

ENV JINDIAO_PROJECT_ROOT=/app
RUN if [ "$JINDIAO_INSTALL_DEPS" = "true" ]; then \
      uv pip install --system --default-index "$PYPI_INDEX_URL" --no-deps .; \
    fi \
    && mkdir -p /app/artifacts \
    && chown -R jindiao:jindiao /app

USER jindiao
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2); raise SystemExit(0 if r.status == 200 else 1)"]

CMD ["python", "-m", "uvicorn", "jindiao.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
