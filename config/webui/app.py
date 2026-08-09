#!/usr/bin/env python3
"""meshinfra Sendefenster - minimales Web-Tool zum Einliefern von Nachrichten.

Liefert ausschliesslich auf {prefix}/tx/# ein, also durch das Rate-Limit-Gate.
Kein Weg an der Mengenbegrenzung vorbei - dieses Tool hat dieselben Rechte wie
ein mosquitto_pub von Hand.

Die Broker-Zugangsdaten bleiben im Container. Der Browser sieht sie nie.
"""

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

BROKER = os.getenv("MQTT_BROKER", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
USERNAME = os.getenv("MQTT_USERNAME") or None
PASSWORD = os.getenv("MQTT_PASSWORD") or None
PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "meshinfra")
MAX_MSG_LEN = int(os.getenv("MAX_MSG_LEN", "140"))
DEFAULT_CHANNEL = int(os.getenv("DEFAULT_CHANNEL", "2"))
BIND = os.getenv("WEBUI_BIND", "0.0.0.0")
HTTP_PORT = int(os.getenv("WEBUI_PORT", "8080"))
# Beschriftung der bekannten Kanaele, Format: "2=#kanal,3=#anderer"
CHANNEL_LABELS = os.getenv("CHANNEL_LABELS", "")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("webui")

state = {"quota": None, "status": "unknown", "dropped": None}
_lock = threading.Lock()


def parse_labels():
    out = []
    for part in filter(None, (p.strip() for p in CHANNEL_LABELS.split(","))):
        idx, _, name = part.partition("=")
        if idx.strip().isdigit():
            out.append({"index": int(idx), "name": name.strip() or f"Kanal {idx}"})
    return out or [{"index": DEFAULT_CHANNEL, "name": f"Kanal {DEFAULT_CHANNEL}"}]


LABELS = parse_labels()


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        log.error("MQTT-Verbindung fehlgeschlagen: %s", reason_code)
        return
    log.info("Mit Broker %s:%d verbunden", BROKER, PORT)
    for t in (f"{PREFIX}/gate/quota", f"{PREFIX}/gate/dropped", f"{PREFIX}/status"):
        client.subscribe(t, qos=1)


def on_message(client, userdata, msg):
    raw = msg.payload.decode("utf-8", errors="replace")
    with _lock:
        if msg.topic.endswith("/gate/quota"):
            try:
                state["quota"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        elif msg.topic.endswith("/gate/dropped"):
            try:
                state["dropped"] = json.loads(raw)
            except json.JSONDecodeError:
                state["dropped"] = {"reason": raw}
        elif msg.topic.endswith("/status"):
            state["status"] = raw


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meshinfra-webui")
if USERNAME:
    client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)


PAGE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>meshinfra — Sendefenster</title>
<style>
  :root { color-scheme: light dark;
    --bg:#fff; --fg:#111; --mut:#666; --line:#d4d4d4; --acc:#0b6; --warn:#b45309; --err:#b00020; --card:#fafafa; }
  @media (prefers-color-scheme: dark) { :root {
    --bg:#141414; --fg:#eee; --mut:#9a9a9a; --line:#333; --acc:#25c07f; --warn:#e0a33e; --err:#ff6b7a; --card:#1c1c1c; } }
  * { box-sizing:border-box }
  body { margin:0; padding:1.5rem 1rem 3rem; background:var(--bg); color:var(--fg);
    font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
  main { max-width:34rem; margin:0 auto }
  h1 { font-size:1.15rem; margin:0 0 .25rem }
  .sub { color:var(--mut); font-size:.85rem; margin:0 0 1.25rem }
  label { display:block; font-size:.85rem; color:var(--mut); margin:1rem 0 .35rem }
  select, textarea, input { width:100%; padding:.6rem .7rem; font:inherit; color:var(--fg);
    background:var(--card); border:1px solid var(--line); border-radius:8px }
  textarea { min-height:7rem; resize:vertical; font-family:ui-monospace,monospace; font-size:.95rem }
  .row { display:flex; justify-content:space-between; align-items:baseline; gap:1rem }
  .count { font-variant-numeric:tabular-nums; font-size:.85rem; color:var(--mut) }
  .count.over { color:var(--err); font-weight:600 }
  button { margin-top:1.25rem; width:100%; padding:.8rem; font:inherit; font-weight:600;
    background:var(--acc); color:#000; border:0; border-radius:8px; cursor:pointer }
  button:disabled { opacity:.45; cursor:not-allowed }
  .bar { display:flex; gap:.5rem 1rem; flex-wrap:wrap; margin:1.5rem 0 0; padding-top:1rem;
    border-top:1px solid var(--line); font-size:.85rem; color:var(--mut) }
  .dot { display:inline-block; width:.55rem; height:.55rem; border-radius:50%; background:var(--mut); margin-right:.35rem }
  .ok { background:var(--acc) } .bad { background:var(--err) }
  #msg { margin-top:1rem; padding:.7rem .8rem; border-radius:8px; font-size:.9rem; display:none }
  #msg.show { display:block }
  #msg.good { background:color-mix(in srgb,var(--acc) 18%,transparent) }
  #msg.bad  { background:color-mix(in srgb,var(--err) 18%,transparent) }
  .hint { font-size:.8rem; color:var(--warn); margin-top:.4rem; display:none }
  .hint.show { display:block }
</style></head>
<body><main>
  <h1>meshinfra — Sendefenster</h1>
  <p class="sub">Geht durch das Rate-Limit. Was hier rausgeht, geht in die Luft.</p>

  <label for="target">Ziel</label>
  <select id="target"></select>

  <div id="destwrap" style="display:none">
    <label for="dest">Empfänger (Kontaktname oder Node-ID)</label>
    <input id="dest" placeholder="Kontaktname" autocomplete="off">
  </div>

  <div class="row"><label for="text" style="margin-bottom:0">Nachricht</label>
    <span class="count" id="count">0 / __MAX__</span></div>
  <textarea id="text" placeholder="Text hier einfügen…"></textarea>
  <div class="hint" id="pubhint">Kanal 0 ist der Gemeinschaftskanal. Automatisiertes gehört dort nicht hin.</div>

  <button id="send" disabled>Senden</button>
  <div id="msg"></div>

  <div class="bar">
    <span><span class="dot" id="dot"></span><span id="status">…</span></span>
    <span id="quota">Kontingent …</span>
    <span id="drop"></span>
  </div>
</main>
<script>
const MAX = __MAX__, CH = __CHANNELS__, DEF = __DEFAULT__;
const $ = id => document.getElementById(id);
const sel = $('target');
for (const c of CH) {
  const o = document.createElement('option');
  o.value = 'chan:' + c.index; o.textContent = c.name + ' (Slot ' + c.index + ')';
  if (c.index === DEF) o.selected = true;
  sel.append(o);
}
sel.append(Object.assign(document.createElement('option'), {value:'direct', textContent:'Direktnachricht…'}));

function refreshForm() {
  const direct = sel.value === 'direct';
  $('destwrap').style.display = direct ? 'block' : 'none';
  const ch = direct ? null : parseInt(sel.value.split(':')[1], 10);
  $('pubhint').classList.toggle('show', ch === 0);
  validate();
}
function validate() {
  const n = $('text').value.length;
  $('count').textContent = n + ' / ' + MAX;
  $('count').classList.toggle('over', n > MAX);
  const destOk = sel.value !== 'direct' || $('dest').value.trim().length > 0;
  $('send').disabled = !(n > 0 && n <= MAX && destOk);
}
sel.oninput = refreshForm; $('text').oninput = validate; $('dest').oninput = validate;

function flash(text, good) {
  const m = $('msg'); m.textContent = text;
  m.className = 'show ' + (good ? 'good' : 'bad');
}
$('send').onclick = async () => {
  $('send').disabled = true;
  const direct = sel.value === 'direct';
  const body = direct
    ? {destination: $('dest').value.trim(), message: $('text').value}
    : {channel: parseInt(sel.value.split(':')[1], 10), message: $('text').value};
  try {
    const r = await fetch('/api/send', {method:'POST', headers:{'content-type':'application/json'},
                                        body: JSON.stringify(body)});
    const j = await r.json();
    if (r.ok) { flash('Eingeliefert. Spätestens in 15 Sekunden in der Luft.', true); $('text').value=''; }
    else flash('Abgelehnt: ' + (j.error || r.status), false);
  } catch (e) { flash('Fehler: ' + e, false); }
  validate(); poll();
};

async function poll() {
  try {
    const s = await (await fetch('/api/state')).json();
    const up = s.status === 'connected';
    $('dot').className = 'dot ' + (up ? 'ok' : 'bad');
    $('status').textContent = up ? 'Node verbunden' : 'Node: ' + s.status;
    $('quota').textContent = s.quota
      ? 'Kontingent ' + s.quota.remaining + ' / ' + s.quota.limit + ' übrig'
      : 'Kontingent unbekannt';
    $('drop').textContent = s.dropped ? 'zuletzt verworfen: ' + s.dropped.reason : '';
  } catch (e) {}
}
refreshForm(); poll(); setInterval(poll, 5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = (
                PAGE.replace("__MAX__", str(MAX_MSG_LEN))
                .replace("__CHANNELS__", json.dumps(LABELS))
                .replace("__DEFAULT__", str(DEFAULT_CHANNEL))
            )
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            with _lock:
                self._send(200, json.dumps(state))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/send":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, json.dumps({"error": f"ungueltiger Body: {e}"}))
            return

        text = (data.get("message") or "").strip()
        if not text:
            self._send(400, json.dumps({"error": "Nachricht ist leer"}))
            return
        # Bewusst ablehnen statt kuerzen: sonst faellt beim Posten der Link weg.
        if len(text) > MAX_MSG_LEN:
            self._send(
                400,
                json.dumps({"error": f"{len(text)} Zeichen, erlaubt sind {MAX_MSG_LEN}"}),
            )
            return

        if "destination" in data:
            dest = (data.get("destination") or "").strip()
            if not dest:
                self._send(400, json.dumps({"error": "Empfaenger fehlt"}))
                return
            topic = f"{PREFIX}/tx/direct"
            payload = json.dumps({"destination": dest, "message": text})
        else:
            ch = data.get("channel")
            if not isinstance(ch, int) or isinstance(ch, bool) or ch < 0:
                self._send(400, json.dumps({"error": "ungueltiger Kanalindex"}))
                return
            topic = f"{PREFIX}/tx/chan"
            payload = json.dumps({"channel": ch, "message": text})

        info = client.publish(topic, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self._send(502, json.dumps({"error": f"Broker lehnt ab (rc={info.rc})"}))
            return
        log.info("Eingeliefert auf %s: %s", topic, payload)
        self._send(200, json.dumps({"ok": True}))


def main():
    client.connect_async(BROKER, PORT, keepalive=60)
    client.loop_start()
    srv = ThreadingHTTPServer((BIND, HTTP_PORT), Handler)
    log.info("Sendefenster auf http://%s:%d", BIND, HTTP_PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
