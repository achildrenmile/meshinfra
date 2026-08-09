# meshinfra — Ausgehende MeshCore-Brücke

Aus der IT-Infrastruktur heraus ins MeshCore-Netz senden. Ein `mosquitto_pub`
auf ein MQTT-Topic genügt. Gegenrichtung (Empfangenes nach MQTT) läuft mit,
weil dasselbe Werkzeug beides kann.

```
Alert / Monitoring / AI-Agent
        │  mosquitto_pub -t meshinfra/tx/chan -m "..."
        ▼
┌───────────────────────────────────────────────────────────┐
│ <STACK_HOST>                                              │
│                                                           │
│   mosquitto ── ACL trennt Einlieferung von Ausführung      │
│      │                                                    │
│      │ meshinfra/tx/#                                     │
│      ▼                                                    │
│   ratelimit ── max N/Stunde, Rest verworfen + geloggt      │
│      │                                                    │
│      │ meshinfra/command/send_chan_msg                     │
│      ▼                                                    │
│   meshcore-mqtt ── 15 s Sendeabstand (eingebaut)           │
└──────┬────────────────────────────────────────────────────┘
       │ TCP 5000 (Companion)
       ▼
   Heltec V3  ──────────────►  868 MHz Mesh
       ▲
       │ zweite, unabhängige TCP-Verbindung
   <OBSERVER_HOST>: meshcore-packet-capture (Observer-Stack, unverändert)
```

Der Node trägt **zwei** gleichzeitige Companion-Clients. Der Observer-Stack auf dem
Observer-Host bleibt unangetastet — anderer Host, anderer Broker, anderer Topic-Prefix.

> Wie das im Einzelnen funktioniert — Sende- und Empfangsweg, Rechtemodell,
> Airtime-Budget, Fehlerbilder, was nachgewiesen ist und was nicht — steht in
> **[ARCHITECTURE.md](ARCHITECTURE.md)**. Dieses README ist die Bedienung.

### Platzhalter in diesem Dokument

Konkrete Adressen stehen **nicht** in der Doku, sondern nur in `.env` und
`/etc/hosts` — beide bleiben lokal. Überall hier gilt:

| Platzhalter | Bedeutung | Wo der echte Wert steht |
|---|---|---|
| `<STACK_HOST>` | Maschine mit diesem Stack | `.env` bzw. lokale Notiz |
| `<OBSERVER_HOST>` | Maschine mit dem Observer-Stack | lokale Notiz |
| `<NODE_IP>` | Heltec V3, Companion TCP 5000 | `MESHCORE_HOST` in `.env` |
| `<KANAL>` | eigener Automatik-Kanal | `CHANNEL_LABELS` in `.env` |
| `<KONTAKT>` | Empfänger einer Direktnachricht | Kontaktliste des Nodes |

> **Namensfalle.** Prüfe einmal mit `getent hosts <name>`, worauf ein
> Hostname tatsächlich zeigt, bevor du ihn in Befehlen benutzt. In diesem
> Aufbau zeigte ein naheliegender Name auf eine **andere** Maschine — die
> Befehle unten benutzen deshalb durchgängig Platzhalter, die du durch die
> IP ersetzt.

---

## Topic-Prefix: `meshinfra`, nicht `meshcore`

`meshcore/...` gehört dem Observer-Stack und trägt *gehörte* Pakete.
`meshinfra/...` trägt *Kommandos* und was dieser Client selbst empfängt.
Getrennt, damit man beim Mitlesen sofort weiß, was man sieht.

> **Der Prefix steht in `config/mosquitto/acl` fest verdrahtet.** Mosquitto
> interpoliert keine Variablen. Wer `MQTT_PREFIX` in `.env` ändert, muss die
> ACL-Datei mit ändern — sonst fällt alles stumm auf „kein Zugriff" zurück.

Zweite Fessel, aus dem Quellcode: `meshcore-mqtt` zerlegt eingehende Topics mit
`topic_parts[1] == "command"`
([`mqtt_worker.py:724`](https://github.com/ipnet-mesh/meshcore-mqtt/blob/main/meshcore_mqtt/mqtt_worker.py#L718-L731)).
Der Prefix muss also **einstufig** sein. `meshinfra` ✓, `mesh/infra` ✗.

---

## Senden

### Sendefenster — die bequeme Variante

**`http://<STACK_HOST>:8080`** — Kanal auswählen, Text einfügen, senden.

Zeigt den Zeichenzähler gegen `MAX_MSG_LEN`, das Restkontingent der Stunde,
den Verbindungszustand des Nodes und die zuletzt verworfene Nachricht.

Das Tool liefert auf dasselbe Topic ein wie ein `mosquitto_pub` von Hand
(`meshinfra/tx/#`) und benutzt dasselbe `infra`-Konto. **Es geht also durch
dasselbe Rate-Limit** — kein Sonderweg, keine Umgehung. Die Broker-Zugangsdaten
bleiben im Container; der Browser bekommt sie nie zu sehen.

Zu lange Nachrichten werden **abgelehnt statt gekürzt**. Beim Ankündigen eines
Beitrags fiele sonst genau der Link am Ende weg.

> Kein Login. Wer im LAN ist, kann senden. Das Rate-Limit begrenzt den Schaden
> auf `RATE_LIMIT_PER_HOUR`. Soll nur der Stack-Host selbst zugreifen:
> `WEBUI_BIND=127.0.0.1` in `.env`.

Die Kanalauswahl kommt aus `CHANNEL_LABELS` in `.env` und muss von Hand
gepflegt werden — Kanäle lassen sich über MQTT nicht abfragen.

### Kanalnachricht

```bash
mosquitto_pub -h <STACK_HOST> -u infra -P "$MQTT_PASS" \
  -t meshinfra/tx/chan -m "Backup fehlgeschlagen"
```

Klartext genügt — das Gate setzt `DEFAULT_CHANNEL` aus `.env` ein. Kanal
explizit:

```bash
mosquitto_pub -h <STACK_HOST> -u infra -P "$MQTT_PASS" \
  -t meshinfra/tx/chan -m '{"channel":1,"message":"Backup fehlgeschlagen"}'
```

### Direktnachricht

Empfänger **muss in der Kontaktliste des Nodes stehen**. Adressiert wird über
Kontaktname oder Node-ID als String — kein Index.

```bash
mosquitto_pub -h <STACK_HOST> -u infra -P "$MQTT_PASS" \
  -t meshinfra/tx/direct -m '{"destination":"<KONTAKT>","message":"Test"}'
```

Bei `tx/direct` ist `destination` Pflicht; Klartext ohne JSON wird verworfen.

---

## Topics

### Einlieferung — hierhin schreibt man (Benutzer `infra`)

| Topic | Payload |
|---|---|
| `meshinfra/tx/chan` | Klartext, oder `{"channel": <int>, "message": "<str>"}` |
| `meshinfra/tx/direct` | `{"destination": "<str>", "message": "<str>"}` |

### Gate-Meldungen — hier steht, was das Rate-Limit getan hat

| Topic | Inhalt |
|---|---|
| `meshinfra/gate/status` | `online` / `offline` (retained, LWT) |
| `meshinfra/gate/quota` | `{"limit":12,"used":3,"remaining":9}` (retained) |
| `meshinfra/gate/dropped` | `{"reason":"...","topic":"...","payload":"..."}` pro verworfener Nachricht |

### Kommandos — schreibt nur das Gate (ACL)

`meshcore-mqtt` abonniert `meshinfra/command/+`. Der Kommandoname ist die
**letzte Topic-Ebene**, die Payload ist JSON. Aus dem Quellcode
([`meshcore_worker.py:345-424`](https://github.com/ipnet-mesh/meshcore-mqtt/blob/main/meshcore_mqtt/meshcore_worker.py#L345-L424)):

| Topic | Payload | Pflichtfelder |
|---|---|---|
| `meshinfra/command/send_chan_msg` | `{"channel": 1, "message": "Text"}` | `channel` (int), `message` (str) |
| `meshinfra/command/send_msg` | `{"destination": "Name", "message": "Text"}` | `destination` (str), `message` (str) |
| `meshinfra/command/device_query` | `{}` | — |
| `meshinfra/command/get_battery` | `{}` | — |
| `meshinfra/command/set_name` | `{"name": "Text"}` | `name` (str) |
| `meshinfra/command/send_advert` | `{"flood": false}` | — |
| `meshinfra/command/send_trace` | `{}` | — |
| `meshinfra/command/send_telemetry_req` | `{"destination": "Name"}` | `destination` |
| `meshinfra/command/send_login` | `{"destination": "Name", "password": "..."}` | beide |
| `meshinfra/command/send_logoff` | `{"destination": "Name"}` | `destination` |

> Das README des Projekts nennt zusätzlich `ping`. Im aktiven Codepfad
> (`bridge_coordinator` → `meshcore_worker`) **gibt es kein `ping`** — nur im
> nicht mehr verdrahteten `meshcore_client.py`. Dafür fehlen im README
> `send_login` und `send_logoff`. Quellcode schlägt Doku.

Payload ohne führende `{` wird nicht als JSON gelesen, sondern zu
`{"data": "<text>"}` — für `send_chan_msg` und `send_msg` also unbrauchbar
([`mqtt_worker.py:740-743`](https://github.com/ipnet-mesh/meshcore-mqtt/blob/main/meshcore_mqtt/mqtt_worker.py#L736-L747)).
Das Gate baut deshalb immer gültiges JSON, auch aus Klartext-Einlieferung.

### Empfangenes und Status — publiziert `meshcore-mqtt`

`meshinfra/message/channel/<idx>`, `meshinfra/message/direct/<pubkey_prefix>`,
`meshinfra/events/connection`, `meshinfra/battery`, `meshinfra/device_info`,
`meshinfra/new_contact`, `meshinfra/advertisement`,
`meshinfra/traceroute/<tag>`, `meshinfra/telemetry`, `meshinfra/contacts`,
`meshinfra/self_info`, `meshinfra/channel_info`, `meshinfra/login`,
`meshinfra/status`.

Alles mitlesen:

```bash
mosquitto_sub -h <STACK_HOST> -u infra -P "$MQTT_PASS" -t 'meshinfra/#' -v
```

---

## Erfolgskontrolle: es gibt keine

**Kein Ergebnis- oder Bestätigungs-Topic.** `meshcore-mqtt` schreibt Erfolg oder
Fehlschlag eines Kommandos nur ins Containerlog
([`meshcore_worker.py:409-421`](https://github.com/ipnet-mesh/meshcore-mqtt/blob/main/meshcore_mqtt/meshcore_worker.py#L409-L421)).
`meshinfra/status` (retained) trägt nur den Verbindungszustand zum Node,
nicht das Schicksal einzelner Nachrichten.

Wer Zustellung wissen muss, liest das Log:

```bash
docker compose logs -f meshcore-mqtt | grep -E "successful|failed"
```

---

## Rate Limit

Zwei Bremsen, sie tun Verschiedenes:

| | Was | Wirkung |
|---|---|---|
| eingebaut | `MESHCORE_MESSAGE_INITIAL_DELAY` / `_SEND_DELAY`, je 15.0 s | **Abstand** zwischen Sendungen |
| eigener Layer | `RATE_LIMIT_PER_HOUR` im `ratelimit`-Container | **Menge** pro rollender Stunde |

Die eingebauten Delays deckeln die Menge nicht — ein Alert-Sturm läuft dort
ungebremst durch, nur langsamer. 868 MHz ist SRD-Band mit Sendezeitbegrenzung.
Deshalb der zweite Layer. **Die 15.0 nicht runterdrehen.**

Was über das Limit hinausgeht, wird **verworfen** (nicht gepuffert) und auf
`meshinfra/gate/dropped` gemeldet. Restkontingent live:

```bash
mosquitto_sub -h <STACK_HOST> -u infra -P "$MQTT_PASS" -t meshinfra/gate/quota -v
```

Das Fenster lebt im Speicher. `docker compose restart ratelimit` setzt es
zurück — praktisch beim Testen, im Betrieb nicht missbrauchen.

Nachrichten über `MAX_MSG_LEN` (140) werden gekürzt und das im Log vermerkt.

### Warum das Limit nicht umgehbar ist

Die ACL (`config/mosquitto/acl`) gibt `infra` Schreibrecht **nur** auf
`meshinfra/tx/#`. Auf `meshinfra/command/#` darf allein der Benutzer `gate`
schreiben. Ein Publish von `infra` direkt auf ein Kommando-Topic wird vom
Broker stillschweigend verworfen — `mosquitto_pub` meldet trotzdem Erfolg,
die Nachricht kommt aber nirgends an. Darum drei getrennte Broker-Konten
(`infra`, `gate`, `bridge`) statt einem.

---

## Betrieb

```bash
cd ~/stacks/meshinfra
docker compose up -d          # hoch
docker compose ps             # mosquitto muss healthy sein
docker compose logs -f        # zusehen
docker compose down           # runter
```

Erstinbetriebnahme siehe unten. Für `mosquitto_pub`/`_sub` ohne lokale
Installation:

```bash
docker run --rm --network meshinfra_meshinfra eclipse-mosquitto:2 \
  mosquitto_pub -h mosquitto -u infra -P "$MQTT_PASS" -t meshinfra/tx/chan -m "Test"
```

### Erstinbetriebnahme

1. `.env` aus `.env.example` erzeugen, `MESHCORE_HOST` und `DEFAULT_CHANNEL`
   ausfüllen.
2. Passwörter erzeugen — **rein alphanumerisch**:
   ```bash
   tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32; echo
   ```
3. Passwortdatei anlegen. Mosquitto liest Benutzer nur gehasht, deshalb der
   Umweg über Wegwerf-Container:
   ```bash
   cd config/mosquitto
   for u in infra gate bridge; do :; done   # Werte aus .env einsetzen
   docker run --rm --user $(id -u):$(id -g) -v "$PWD":/work eclipse-mosquitto:2 \
     mosquitto_passwd -c -b /work/passwd infra 'PASS_INFRA'
   docker run --rm --user $(id -u):$(id -g) -v "$PWD":/work eclipse-mosquitto:2 \
     mosquitto_passwd -b /work/passwd gate 'PASS_GATE'
   docker run --rm --user $(id -u):$(id -g) -v "$PWD":/work eclipse-mosquitto:2 \
     mosquitto_passwd -b /work/passwd bridge 'PASS_BRIDGE'
   docker run --rm -v "$PWD":/work alpine:3 \
     sh -c 'chown 1883:1883 /work/passwd && chmod 600 /work/passwd'
   cd ../..
   ```
   `-c` legt die Datei **neu** an und wirft bestehende Benutzer weg — beim
   Hinzufügen also ohne `-c`. Eigentümer **1883** ist die UID, unter der
   Mosquitto im Container läuft; ohne das startet der Broker nicht. `600`
   heißt, der Host-Benutzer kann sie danach selbst nicht mehr lesen. Absicht.
4. `docker compose up -d`

### Am Node vorbereiten (nicht Teil des Stacks)

- **Eigener Kanal** für automatisierte Meldungen. Nicht der Hauptkanal, nicht
  der Gemeinschaftskanal des Regionalnetzes. Kanalindex nach
  `DEFAULT_CHANNEL` in `.env`.
- Für Direktnachrichten: Empfänger muss in der **Kontaktliste** des Nodes
  stehen.

Kanalbelegung am Node (Beispiel):

| Slot | Name | Secret |
|---|---|---|
| 0 | `Public` | `<SECRET>` — Gemeinschaftskanal, **nicht** für Automatik |
| 2 | `<KANAL>` | `<SECRET>` |

`meshcore-mqtt` kann Kanäle **nicht** abfragen oder setzen — es abonniert zwar
das Event `CHANNEL_INFO`, kennt aber kein passendes Kommando. Dafür direkt die
Bibliothek benutzen:

```bash
docker run --rm --network host --entrypoint python meshinfra-meshcore-mqtt:v0.1.3 -c "
import asyncio, hashlib
from meshcore import MeshCore
async def main():
    mc = await MeshCore.create_tcp('<NODE_IP>', 5000)
    for i in range(5):
        print(i, (await mc.commands.get_channel(i)).payload)
    await mc.disconnect()
asyncio.run(main())"
```

> Das öffnet eine **dritte** Verbindung zum Node. Beobachtet: der laufende
> Bridge-Container quittiert das gelegentlich mit
> `health check failed, attempting recovery` und fängt sich binnen einer
> Sekunde wieder. Zwei Clients sind unauffällig, der dritte ist es nicht ganz.
> Solche Abfragen also sparsam und nicht im Dauerbetrieb.

#### Hash-Kanäle: der Name **ist** der Schlüssel

Bei Kanälen, deren Name mit `#` beginnt, leitet MeshCore das Secret aus dem
Namen ab: **die ersten 16 Byte von `sha256(name)`, inklusive des `#`.**

```bash
python3 -c "import hashlib; print(hashlib.sha256('<KANAL>'.encode()).hexdigest()[:32])"
# <SECRET>
```

Gegengeprüft an den dokumentierten Beispielen: `#test` →
`9cd8fcf22a47333b591d96a2b848b73f`, `#chicago` →
`c1c289b131e5222370cbc2048445844b`. Beide stimmen exakt.

Zwei Folgen daraus:

1. **Zum Mitlesen genügt der Name.** Wer `<KANAL>` kennt oder errät, hat den
   Schlüssel — bei zwei Zeichen ist das kein Kunststück. Ein Hash-Kanal ist
   öffentlich, nur unbeschriftet. Nichts darüber senden, was nicht mitgelesen
   werden darf.
2. **Ein zweites Gerät braucht keinen Schlüsselaustausch.** Dort denselben
   Kanalnamen `<KANAL>` eintragen, fertig — das Secret entsteht auf beiden Seiten
   gleich.

Kanal setzen:

```bash
docker run --rm --network host --entrypoint python meshinfra-meshcore-mqtt:v0.1.3 -c "
import asyncio, hashlib
from meshcore import MeshCore
NAME='<KANAL>'
async def main():
    mc = await MeshCore.create_tcp('<NODE_IP>', 5000)
    print(await mc.commands.set_channel(2, NAME, hashlib.sha256(NAME.encode()).digest()[:16]))
    await mc.disconnect()
asyncio.run(main())"
```

---

## Die Quoting-Falle in `.env`

Hat in diesem Projekt zweimal zugeschlagen — einmal am Node beim WLAN-Passwort,
einmal beim Broker.

- **Keine Anführungszeichen** um Werte. Compose nimmt sie wörtlich mit.
- **Kein `#`** — leitet einen Kommentar ein, der Rest des Wertes ist weg.
- **Kein `$`** — Compose interpoliert das als Variable.

Deshalb Passwörter rein alphanumerisch. Nach dem Start prüfen, dass der
Container den **vollständigen** String sieht:

```bash
docker compose exec meshcore-mqtt printenv MQTT_PASSWORD | od -c
```

Erwartet: die 32 Zeichen, dann `\n`. Nichts abgeschnitten, keine `"`.

---

## Troubleshooting

| Symptom | Ursache | Abhilfe |
|---|---|---|
| Broker startet nicht, Log nennt `passwd` | Datei gehört nicht UID 1883 oder fehlt | `chown 1883:1883`-Schritt oben wiederholen |
| `mosquitto_pub` meldet Erfolg, nichts passiert | ACL greift — falscher Benutzer oder falsches Topic | mit `infra` auf `meshinfra/tx/#` publizieren, nicht auf `command/` |
| Gar nichts kommt an, keine Fehlermeldung | `MQTT_PREFIX` geändert, `config/mosquitto/acl` nicht | Prefix in der ACL nachziehen |
| Gate protokolliert `VERWORFEN (Rate-Limit erreicht ...)` | Stundenkontingent aufgebraucht | warten, `RATE_LIMIT_PER_HOUR` erhöhen, oder zum Testen `docker compose restart ratelimit` |
| `meshcore-mqtt` verbindet nicht | `MESHCORE_HOST` leer/falsch, Node aus, Port belegt | `timeout 2 bash -c 'echo > /dev/tcp/<IP>/5000' && echo ok` |
| Kommando kurz nach Neustart verpufft | 5 s Startup-Grace-Period ignoriert Kommandos ([`meshcore_worker.py:326`](https://github.com/ipnet-mesh/meshcore-mqtt/blob/main/meshcore_mqtt/meshcore_worker.py#L326)) | 5 s warten |
| Nach jedem Neustart geht dieselbe Nachricht raus | irgendwo `retain=true` auf einem `command/`-Topic | `MQTT_RETAIN` muss `false` bleiben; retained Kommando mit leerer Payload löschen |
| Passwort wird nicht akzeptiert, sieht aber richtig aus | Quoting-Falle | `printenv ... | od -c`, siehe oben |

### Node verträgt die zweite Verbindung nicht?

Erkennbar daran, dass der Observer-Stack auf <OBSERVER_HOST> nach dem Start dieses
Stacks keine Pakete mehr liefert. Dann ist die Grundannahme falsch und die
Architektur muss neu bewertet werden — nicht mit Reconnect-Schleifen
übertünchen.

---

## Randbedingungen

- <OBSERVER_HOST> und der Observer-Stack werden **nicht** verändert
- Keine Secrets in Git: `.env` und `config/mosquitto/passwd` sind gitignored
- Broker bleibt LAN-intern, kein Traefik, keine Exposition nach außen
- Kein Reflash des Nodes
- Automatisierte Meldungen gehen auf den **eigenen** Kanal, nie auf den
  Gemeinschaftskanal des Regionalnetzes
