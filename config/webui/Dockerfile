FROM python:3.12-alpine

RUN pip install --no-cache-dir "paho-mqtt>=2.1,<3"

RUN adduser -D -u 10002 webui
USER webui

COPY app.py /app/app.py
WORKDIR /app

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/app.py"]
