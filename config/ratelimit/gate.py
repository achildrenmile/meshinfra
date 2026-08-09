#!/usr/bin/env python3
"""Rate-Limit-Gate vor der MeshCore-Bruecke.

Einlieferung:   {prefix}/tx/chan      JSON {"channel": N, "message": "..."} oder Klartext
                {prefix}/tx/direct    JSON {"destination": "...", "message": "..."}

Weitergabe:     {prefix}/command/send_chan_msg
                {prefix}/command/send_msg

Meldungen:      {prefix}/gate/status   online|offline (retained, LWT)
                {prefix}/gate/quota    {"limit":N,"used":N,"remaining":N} (retained)
                {prefix}/gate/dropped  JSON pro verworfener Nachricht

Warum ueberhaupt: 868 MHz ist SRD-Band mit Sendezeitbegrenzung. Die Delays in
meshcore-mqtt entzerren nur den Abstand zwischen Sendungen (15 s), sie deckeln
nicht die Menge pro Stunde. Ein Alert-Sturm laeuft dort ungebremst durch, nur
langsamer. Dieses Gate deckelt die Menge und verwirft den Rest.
"""

import json
import logging
import os
import signal
import sys
import time
from collections import deque

import paho.mqtt.client as mqtt

BROKER = os.getenv("MQTT_BROKER", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
USERNAME = os.getenv("MQTT_USERNAME") or None
PASSWORD = os.getenv("MQTT_PASSWORD") or None
PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "meshinfra")
LIMIT = int(os.getenv("RATE_LIMIT_PER_HOUR", "12"))
DEFAULT_CHANNEL = int(os.getenv("DEFAULT_CHANNEL", "1"))
MAX_MSG_LEN = int(os.getenv("MAX_MSG_LEN", "140"))
WINDOW = 3600.0

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("gate")

T_TX = f"{PREFIX}/tx/+"
T_STATUS = f"{PREFIX}/gate/status"
T_QUOTA = f"{PREFIX}/gate/quota"
T_DROPPED = f"{PREFIX}/gate/dropped"
T_CHAN = f"{PREFIX}/command/send_chan_msg"
T_DIRECT = f"{PREFIX}/command/send_msg"

# Zeitstempel der durchgelassenen Sendungen, rollendes Fenster von einer Stunde.
sent = deque()


def prune(now):
    while sent and now - sent[0] >= WINDOW:
        sent.popleft()


def publish_quota(client):
    now = time.monotonic()
    prune(now)
    payload = json.dumps(
        {"limit": LIMIT, "used": len(sent), "remaining": max(0, LIMIT - len(sent))}
    )
    client.publish(T_QUOTA, payload, qos=1, retain=True)


def drop(client, reason, topic, raw):
    log.warning("VERWORFEN (%s) topic=%s payload=%r", reason, topic, raw[:200])
    client.publish(
        T_DROPPED,
        json.dumps({"reason": reason, "topic": topic, "payload": raw[:200]}),
        qos=1,
        retain=False,
    )


def parse(topic, raw):
    """-> (command_topic, payload_dict) oder (None, reason)."""
    kind = topic.rsplit("/", 1)[-1]
    raw = raw.strip()
    if not raw:
        return None, "leere Nachricht"

    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"kein gueltiges JSON: {e}"
        if not isinstance(data, dict):
            return None, "JSON ist kein Objekt"
        text = data.get("message", "")
    else:
        # Klartext - damit ein simples mosquitto_pub reicht.
        data = {}
        text = raw

    if not isinstance(text, str) or not text.strip():
        return None, "Feld 'message' fehlt oder ist leer"
    text = text.strip()
    if len(text) > MAX_MSG_LEN:
        log.warning("Nachricht auf %d Zeichen gekuerzt (war %d)", MAX_MSG_LEN, len(text))
        text = text[:MAX_MSG_LEN]

    if kind == "chan":
        channel = data.get("channel", DEFAULT_CHANNEL)
        if isinstance(channel, str) and channel.isdigit():
            channel = int(channel)
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
            return None, f"ungueltiger Kanalindex: {channel!r}"
        return T_CHAN, {"channel": channel, "message": text}

    if kind == "direct":
        dest = data.get("destination")
        if not isinstance(dest, str) or not dest.strip():
            return None, "Feld 'destination' fehlt (bei tx/direct Pflicht)"
        return T_DIRECT, {"destination": dest.strip(), "message": text}

    return None, f"unbekanntes Einlieferungs-Topic: {kind}"


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        log.error("MQTT-Verbindung fehlgeschlagen: %s", reason_code)
        return
    log.info("Mit Broker %s:%d verbunden, Limit %d/Stunde", BROKER, PORT, LIMIT)
    client.subscribe(T_TX, qos=1)
    log.info("Abonniert: %s", T_TX)
    client.publish(T_STATUS, "online", qos=1, retain=True)
    publish_quota(client)


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    log.warning("Verbindung zum Broker verloren: %s", reason_code)


def on_message(client, userdata, msg):
    raw = msg.payload.decode("utf-8", errors="replace")
    target, result = parse(msg.topic, raw)
    if target is None:
        drop(client, result, msg.topic, raw)
        return

    now = time.monotonic()
    prune(now)
    if len(sent) >= LIMIT:
        wait = int(WINDOW - (now - sent[0]))
        drop(
            client,
            f"Rate-Limit erreicht ({LIMIT}/Stunde), naechster Slot in {wait}s",
            msg.topic,
            raw,
        )
        publish_quota(client)
        return

    sent.append(now)
    client.publish(target, json.dumps(result), qos=1, retain=False)
    log.info(
        "DURCHGELASSEN -> %s %s (%d/%d in dieser Stunde)",
        target,
        json.dumps(result, ensure_ascii=False),
        len(sent),
        LIMIT,
    )
    publish_quota(client)


def main():
    if LIMIT < 1:
        log.error("RATE_LIMIT_PER_HOUR muss >= 1 sein, ist %d", LIMIT)
        sys.exit(1)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="meshinfra-ratelimit"
    )
    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)
    client.will_set(T_STATUS, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def stop(signum, frame):
        log.info("Signal %s - beende", signum)
        client.publish(T_STATUS, "offline", qos=1, retain=True)
        client.disconnect()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        try:
            client.connect(BROKER, PORT, keepalive=60)
            break
        except OSError as e:
            log.warning("Broker noch nicht erreichbar (%s), neuer Versuch in 5s", e)
            time.sleep(5)

    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
