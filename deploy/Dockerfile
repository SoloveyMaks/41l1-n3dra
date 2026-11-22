# syntax=docker/dockerfile:1

# Base images are pinned by digest for reproducible builds.
ARG NODE_IMAGE=docker.io/library/node:22-bookworm-slim@sha256:47725aca53d3295ec2d3be8f62726599b3033cc262edc2c7e429909b3ac599fc
ARG PYTHON_IMAGE=docker.io/library/python:3.12-slim@sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177

FROM ${NODE_IMAGE} AS frontend-build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund
# The production SecurityMap and its canonical types live in sibling packages.
COPY frontend ./frontend
COPY ui ./ui
COPY docs ./docs
RUN cd frontend && npm run build

FROM ${NODE_IMAGE} AS node-runtime
# Runtime dependency for packages/comparison. Only the node binary is copied;
# npm/npx and dev tooling stay out of the final image.

FROM ${PYTHON_IMAGE} AS python-wheels
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
COPY deploy/requirements.txt /tmp/requirements.txt
RUN python -m pip wheel --wheel-dir /wheels -r /tmp/requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ARG NEDRA_REVISION
LABEL org.opencontainers.image.revision=${NEDRA_REVISION}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CG_DATA_DIR=/app/data \
    CG_NODE_BIN=/usr/local/bin/node \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 nedra \
    && useradd --uid 10001 --gid nedra --home-dir /app --no-create-home --shell /usr/sbin/nologin nedra

WORKDIR /app
COPY deploy/requirements.txt /tmp/requirements.txt
COPY --from=python-wheels /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt
# Custom checks run user Python only inside bubblewrap with prlimit limits.
# Verified in the rootless podman runtime (read-only rootfs, cap-drop=ALL):
# bwrap starts a nested namespace and blocks network/`/etc` access, see
# docs/custom-checks.md. Without bwrap the API reports the runner unavailable
# and refuses to execute user code on the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap util-linux \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY backend ./backend
COPY analyzers ./analyzers
COPY rag ./rag
COPY db ./db
COPY docs ./docs
COPY packages/comparison ./packages/comparison
COPY deploy/healthcheck.py ./deploy/healthcheck.py
COPY deploy/requirements.txt ./deploy/requirements.txt
COPY scripts/nedra_manifest.py /usr/local/bin/nedra-manifest.py
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /src/frontend/dist ./frontend/dist
# Fail the build if the pinned Node binary cannot use the Python-slim libs.
RUN /usr/local/bin/node --version
RUN python /usr/local/bin/nedra-manifest.py --revision "$NEDRA_REVISION" \
    && chmod -R a+rX /app /usr/local/bin/nedra-manifest.py

RUN install -d -o nedra -g nedra /app/data
USER nedra
RUN python /usr/local/bin/nedra-manifest.py --revision "$NEDRA_REVISION" --verify
EXPOSE 18080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]

# Bind all interfaces inside the container; the host publishes only 127.0.0.1:18080.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "18080", "--no-access-log"]
