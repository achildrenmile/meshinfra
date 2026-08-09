FROM python:3.12-alpine

RUN pip install --no-cache-dir "paho-mqtt>=2.1,<3"

RUN adduser -D -u 10001 gate
USER gate

COPY gate.py /app/gate.py
WORKDIR /app

# Ungepuffert, sonst stehen die Drop-Meldungen erst mit Verspaetung im Log.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/gate.py"]
