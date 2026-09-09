# Baut ipnet-mesh/meshcore-mqtt aus einem gepinnten Upstream-Tag, mit
# lokalen Korrekturen in mqtt_worker.py und meshcore_worker.py - siehe
# README.md in diesem Verzeichnis fuer Begruendung und Herkunft der Patches.
#
# Struktur (Dependencies/Runtime-Stage) 1:1 aus dem Upstream-Dockerfile
# uebernommen, nur um die "source"-Stage ergaenzt, die klont und den
# lokalen Patch einspielt.

ARG MESHCORE_MQTT_VERSION=v0.1.3

FROM python:3.12-alpine AS source
ARG MESHCORE_MQTT_VERSION
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --branch ${MESHCORE_MQTT_VERSION} --depth 1 \
      https://github.com/ipnet-mesh/meshcore-mqtt.git .
# Lokale Patches (siehe README.md) - ersetzen die Upstream-Dateien komplett.
COPY mqtt_worker.py meshcore_mqtt/mqtt_worker.py
COPY meshcore_worker.py meshcore_mqtt/meshcore_worker.py

FROM python:3.12-alpine AS builder
RUN apk add --no-cache \
    gcc \
    musl-dev \
    linux-headers \
    libffi-dev \
    openssl-dev \
    cargo \
    rust
WORKDIR /app
COPY --from=source /src/requirements.txt /src/requirements-dev.txt ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip wheel setuptools && \
    pip install --no-cache-dir -r requirements.txt
COPY --from=source /src/meshcore_mqtt/ ./meshcore_mqtt/
COPY --from=source /src/pyproject.toml ./
RUN pip install --no-cache-dir .

FROM python:3.12-alpine AS runtime
LABEL description="Production runtime for MeshCore MQTT Bridge (mit lokalem Reconnect-Fix)"
RUN apk add --no-cache \
    ca-certificates \
    tzdata \
    tini
RUN addgroup -g 1000 meshcore && \
    adduser -D -u 1000 -G meshcore -s /bin/sh meshcore
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app"
RUN mkdir -p /app/logs && \
    chown -R meshcore:meshcore /app
WORKDIR /app
RUN adduser meshcore dialout
USER meshcore
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import meshcore_mqtt; print('Health check passed')" || exit 1
ENV LOG_LEVEL=INFO \
    MQTT_BROKER=localhost \
    MQTT_PORT=1883 \
    MQTT_TOPIC_PREFIX=meshcore \
    MESHCORE_CONNECTION=serial \
    MESHCORE_ADDRESS=/dev/ttyUSB0 \
    MESHCORE_BAUDRATE=115200
EXPOSE 1883
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python", "-m", "meshcore_mqtt.main", "--env"]
