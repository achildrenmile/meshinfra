# meshcore-mqtt (patched build)

Baut [ipnet-mesh/meshcore-mqtt](https://github.com/ipnet-mesh/meshcore-mqtt)
aus einem gepinnten Upstream-Tag, mit zwei lokalen Korrekturen:
`mqtt_worker.py` (Reconnect-Fix) und `meshcore_worker.py`
(Stale-Timeout-Fix, siehe unten).

## Patch 1: `mqtt_worker.py` (Reconnect nach MQTT-Disconnect)

**Problem**: `paho-mqtt` ruft den `on_disconnect`-Callback in seinem eigenen
Netzwerk-Thread auf - dort laeuft kein asyncio-Event-Loop. Der
Original-Code rief in diesem Callback `asyncio.create_task(...)` auf, was
dort eine `RuntimeError("no running event loop")` wirft. `paho` verschluckt
diese Exception intern, wodurch die Bruecke nach einem MQTT-Disconnect nie
wieder reconnectet - der Prozess laeuft weiter und sieht "healthy" aus
(der eingebaute Healthcheck prueft nur, ob der Prozess lebt), aber MQTT
bleibt dauerhaft down.

**Fix**: dieselbe Technik verwenden, die der Upstream-Code an anderer
Stelle in derselben Datei bereits fuer genau dieses Problem einsetzt
(`_forward_command_to_meshcore`) - die Recovery-Coroutine per
`asyncio.run_coroutine_threadsafe(...)` auf den beim Start gespeicherten
Event-Loop (`self._event_loop`) planen, statt `asyncio.create_task()`
direkt im Callback aufzurufen.

**Herkunft dieser Datei** (2026-09-09): der urspruengliche Patch wurde vor
der GHCR-Build-Pipeline von Hand angewendet und nur als fertiges Image
(`ghcr.io/achildrenmile/meshinfra-meshcore-mqtt:v0.1.3-patched`) gepusht -
der Quellcode selbst war in keinem Repo committet. Diese Datei wurde aus
dem LAUFENDEN Produktions-Container zurueckextrahiert
(`/opt/venv/lib/python3.12/site-packages/meshcore_mqtt/mqtt_worker.py`)
und per Diff gegen den unveraenderten Upstream-Tag `v0.1.3` verifiziert -
es ist die exakt gleiche Aenderung, nur nachtraeglich in Git nachgezogen.

## Patch 2: `meshcore_worker.py` (Stale-Timeout zu aggressiv fuer ruhige Kanaele)

**Problem** (gefunden 2026-09-09, dcsetup-Betrieb `dc-noetsch`): `!frag`/
`!ping` u.ae. wurden von `meshbot` nur unzuverlaessig beantwortet, obwohl
Bot und Bridge laut Status-Log durchgehend liefen. Ursache: `_perform_health_check()` erklaert die MeshCore-Verbindung fuer "stale", sobald seit
`_last_activity` mehr als `timeout_seconds` (Upstream-Default: **180s**)
vergangen sind - UNABHAENGIG davon, ob `_check_connection_health()` (der
eigentliche technische Verbindungstest) die Verbindung fuer gesund haelt.
Auf einem Amateurfunk-Mesh-Kanal mit sporadischem Traffic ist 180s
Inaktivitaet der Normalfall, kein Fehler - das loeste im Produktivbetrieb
alle paar Minuten einen unnoetigen Full-Reconnect aus
(`meshcore.connect()` neu). Waehrend der ca. 1s dauernden Reconnect-Luecke
gehen eingehende Kanalnachrichten vom Companion-Radio verloren, ohne dass
irgendein Zaehler (Queue/Dropped) das festhaelt - dadurch sah der Bot in
jedem Monitoring "gesund" aus, obwohl Befehle sporadisch nie ankamen.

**Fix**: `timeout_seconds` in genau diesem einen Aufruf (`_perform_health_check`, Zeile mit `self._is_stale(...)`) von 180 auf 1800 (30 Minuten)
angehoben. Der echte Verbindungstest (`_check_connection_health()`) bleibt
unveraendert und erkennt einen tatsaechlichen Ausfall weiterhin sofort -
nur die reine Inaktivitaets-Heuristik wurde entschaerft. Diese Datei ist
sonst 1:1 Upstream `v0.1.3`.

## Build

```bash
docker build --build-arg MESHCORE_MQTT_VERSION=v0.1.3 -t meshinfra-meshcore-mqtt:v0.1.3-patched .
```

Bei einem Upstream-Versions-Bump: `MESHCORE_MQTT_VERSION` erhoehen, pruefen
ob der Patch noch sauber greift (falls `mqtt_worker.py` sich upstream
geaendert hat, ggf. manuell nachziehen statt blind zu ueberschreiben).
