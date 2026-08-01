# ATI Straton — Home Assistant Integration

**Projekt:** `ati_straton` — Custom Integration für Home Assistant
**Zielgerät:** ATI **Straton Flex G2 153**, Firmware **3.0.4** (Release 2024.4), `http://192.168.1.42`
**Dokumentstatus:** v0.2 — Planung, Architektur, Schnittstellen · **Phase 0 abgeschlossen**
**Stand:** 2026-07-31

> **Verifikationsgrad:** Alle Endpunkte, Payloads und Events in diesem Dokument wurden
> gegen das reale Gerät mit authentifizierter Session **verifiziert**. Ausnahmen sind
> ausdrücklich als *unbestätigt* markiert. Die aufgezeichneten Antworten liegen als
> Fixtures vor und dienen als Testgrundlage (NFR-07).

---

## 1. Zielsetzung

Anbindung einer ATI Straton Aquarien-LED-Leuchte an Home Assistant mit möglichst
vollständigem Funktionsumfang. Das Gerät bietet keine dokumentierte öffentliche API;
die Schnittstelle wurde aus dem ausgelieferten AngularJS-Frontend rekonstruiert und
am Gerät verifiziert.

### 1.1 Leitziele

| # | Ziel | Priorität | Machbarkeit nach Phase 0 |
|---|------|-----------|--------------------------|
| Z1 | Live-Zustand in HA (Intensität, Temperatur, Strom, Online-Status) | Muss | ✅ gesichert |
| Z2 | Steuerung der Leuchte aus HA | Muss | ✅ gesichert — zwei unabhängige Pfade gefunden |
| Z3 | Push-Updates statt reinem Polling | Soll | ✅ gesichert (Socket.IO) |
| Z4 | Tagesverlauf (Timelines) lesbar | Soll | ✅ gesichert |
| Z5 | Tagesverlauf aus HA veränderbar | Kann | ⚠️ möglich, aber riskant (§3) |
| Z6 | Config Flow (UI), keine YAML-Pflicht | Muss | ✅ gesichert |
| Z7 | Keine Inkonsistenz am Gerät durch die Integration | Muss | ✅ durch Designentscheidungen §3/§4.4 |

### 1.2 Nicht-Ziele

- Kein Ersatz der ATI-Weboberfläche für Erstinbetriebnahme (`/setup`), WLAN-Einrichtung,
  Firmware-Update oder Spot-Verwaltung.
- Keine Firmware-Modifikation, kein Eingriff unterhalb der HTTP-/WebSocket-Ebene.
- Keine Cloud-Anbindung — ausschließlich lokale Kommunikation.

---

## 2. Gerät und Bestandsaufnahme

### 2.1 Identität (verifiziert)

| Merkmal | Wert | Quelle |
|---------|------|--------|
| Gerätetyp | `Straton Flex G2 153` | `/api/info` → `deviceType` |
| Geräte-ID | `10000001` | `/api/info` → `id` |
| Hostname | `ATI-Straton-10000001` | `/api/hostname` |
| Firmware | `3.0.4`, `2024-12-16`, Release `2024.4` | `/api/version` |
| Rolle | Master (`isMaster: true`), keine Slave-Geräte (`/api/devices` → `[]`) | `/api/info`, `/api/devices` |
| Uptime | 111 Tage (zum Erfassungszeitpunkt) | `/api/uptime` |
| Anbindung | WLAN, Kanal 11, `signalStrength: "50"`, `connected: true` | `/api/wifistatus` |
| Zeitzone | `Europe/Berlin` (`CET-1CEST,M3.5.0,M10.5.0/3`) | `/api/timezone` |
| ADC-Hardware | vorhanden (`adc: true`) | `/api/info` |
| Temperaturgrenze | `maxTemperature: 60` | `/api/info` |

**Ableitung für HA:** Eindeutiger Identifier = `10000001`. `configuration_url` = Geräte-URL.
`sw_version` = `/api/version.number`. `model` = `deviceType`.

### 2.2 Technologie-Stack

| Merkmal | Beobachtung |
|---------|-------------|
| Server | Node.js / **Express** (`X-Powered-By: Express`) |
| Frontend | AngularJS 1.x, jQuery, Bootstrap, lodash, D3 v7, `angular-translate` |
| Auth | **Express-Session**, Cookie `connect.sid` (`HttpOnly`, `Path=/`), Form-POST auf `/login` |
| Realtime | **Socket.IO v4** (`EIO=4`, `pingInterval` 25 s, `pingTimeout` 60 s, Upgrade auf WebSocket) |
| TLS | **nicht vorhanden** — nur HTTP |

Client-Bundles: `js/ati-login.min.js` (Login), `js/ati.min.js` (Shell/Routing),
**`js/controller/controller.min.js`** (die eigentliche Anwendungslogik — enthält den
Großteil der API-Aufrufe), `js/angular-chart/ati-chart.min.js`, `js/ati-setup.min.js`.

### 2.3 Datenmodell (verifiziert)

Das Gerät ist **kein einfacher Dimmer**, sondern ein **zeitplangesteuerter
Mehrkanal-LED-Controller**.

```
Device (id 10000001, "Straton Flex G2 153")
│
├── channels[6]      Physische Farbkanäle, Wertebereich 0–255
│                    W(0) · V(1) · RB(2) · B(3) · LC(4) · R(5)
│                    je: { id, name, label, value, sort, factor,
│                          valueTemperature?: { name, temperature, max } }
│                    → factor: W/V/RB/B/LC = 1.0, R = 0.8 (Leistungsgewicht)
│
├── spots[3]         Physische LED-Module: "Spot_SiriusPro 1…3"
│                    je: { _id, name, active, enabled,
│                          channels[]: { name, channel, sort, max, address } }
│                    → externalId-Format: "<deviceId>:<spot._id>"  z. B. "10000001:0"
│                    → channel.max = individuelles Strom-/Leistungslimit je Kanal
│
├── colors[10]       **Farbpresets** (Spektren), NICHT Kanäle
│                    "Farbe A", "Farbe B", "Farbe C", …
│                    je: { _id, name, values[]: { id, name, label, value 0–255, sort } }
│
├── timelines[1]     Dimmkurven / "Sections"
│                    { _id, name: "GROUP_NAME_SEC", visible, active, linecolor,
│                      colorEditable, presetting: {id,title}, spotAddresses[1..6],
│                      spots[], nodes[] }
│                    └── nodes[16]  Stützpunkte der Tageskurve
│                        { time: <Sekunden seit Mitternacht>, value: <Intensität>,
│                          type: "first"|"node"|…, index, valueOrg, color: <Farbpreset> }
│                        → jeder Knoten trägt Zeit + Intensität + vollständiges Farbpreset
│
└── presettings[7]   Werkspresets, z. B. "werkspreset.json"
                     { _id, filename, title, description, group, disabled,
                       timerange: { start, end } (Sek.), intensities[] }
```

**Wichtig:** `colors` sind Spektren-Presets, `channels` sind die Hardware-Kanäle.
Die Namensgleichheit der Felder (`values[].name` ↔ `channels[].name`) verbindet beide.

---

## 3. Zentrale architektonische Erkenntnis

Phase 0 hat **drei** Steuerpfade zutage gefördert, mit sehr unterschiedlichem Risiko.
Die Wahl zwischen ihnen ist die wichtigste Entwurfsentscheidung des Projekts.

> **⚠️ Überholt durch Messungen vom 31.07.2026.** Die folgende Bewertung der drei
> Pfade stammt aus der Analyse des Frontend-Codes. Am Gerät gilt:
>
> | Pfad | Tatsächlich |
> |------|-------------|
> | **A** — Socket `color-preview`/`color-change` | ❌ **wirkungslos**, auch mit protokollkonformem Engine.IO-3-Client |
> | **B** — `POST /api/demo` | ungetestet |
> | **C** — `PUT /api/data` | ✅ **funktioniert**, Wirkung < 3 s, exakt umkehrbar |
>
> Maßgeblich ist §4.9. Der Abschnitt hier bleibt als Herleitung stehen.

### Pfad A — Live-Preview über Socket.IO ❌ *am Gerät wirkungslos*

```js
socket.emit("color-preview", { enabled: true })              // Preview-Modus betreten
socket.emit("color-change", { value: 0…255, name: "<Kanal>", spots: ["10000001:0", …] })
socket.emit("color-preview", { enabled: false })             // verlassen → Zeitplan gilt wieder
```

- **Granular:** einzelner Kanal, einzelne Spots, Wertebereich 0–255
  (Client-Validierung: `value < 0 || value > 255` → ungültig).
- **Transient:** verändert den persistierten Tagesverlauf **nicht**.
- Das Original-Frontend sendet `color-preview {enabled:false}` beim `$destroy` des
  Controllers — Verlassen der Seite stellt den Automatikbetrieb wieder her.
- `spots` wird im Frontend aus allen **sichtbaren** Timelines gebildet
  (`_.uniqBy` über `spot.externalId`).
- Der Zustand ist über `/api/status` (`isColorPreview`, `channels[].value`) auslesbar.

**Das ist der ideale HA-Steuerpfad:** granular, rückstandsfrei, verlustfrei auf
`light`/`number`-Entitäten abbildbar.

### Pfad B — Demo-/Manuell-Modus über REST ✅ *ergänzend*

```http
POST /api/demo    { color, sections: [<timeline._id>], intensity, duration, active, start }
```

- Start: `{ …demoModel, start: undefined, active: true }`
- Stopp: `{ …demoModel, active: false }`
- Antwort spiegelt das gespeicherte Modell; `GET /api/demo` liefert `null`, wenn inaktiv
  (aktuell am Gerät: **`null`** = kein Demo-Modus aktiv).
- Grobkörniger als Pfad A (wirkt auf ganze Sections mit einer Gesamt-`intensity` und
  einem Farbpreset), dafür **persistent** und überlebt einen Verbindungsabbruch.

### Pfad C — Zeitplan schreiben ⚠️ *nur mit Opt-in*

```http
PUT /api/data       { timelines, spots, colors }   →   { lines, spots, colors }
PUT /api/timeline   (Einzel-Timeline, Payload unbestätigt)
```

- `PUT /api/data` ist eine **Vollersetzung** des gesamten Dokuments.
- **Kein ETag, kein Versionsfeld** → Lost Updates gegenüber der Weboberfläche möglich.
- Ein fehlerhafter Schreibvorgang zerstört den Tagesverlauf des Aquarienlichts —
  mit realem Risiko für den Tierbestand.
- **Bestätigte Asymmetrie:** Request sendet `timelines`, Response liefert `lines`.

**Entscheidung:** Steuerung erfolgt über **Pfad A** (primär) und **Pfad B** (für
persistente Overrides). **Pfad C bleibt hinter einem expliziten Opt-in** im Options
Flow und mit Pflicht-Backup (FR-12). Damit ist Z7 gewahrt.

---

## 4. Architektur

### 4.1 Übersicht

```
┌──────────────────────────── Home Assistant ────────────────────────────┐
│                                                                        │
│  config_flow.py       Host · Benutzer · Passwort · Optionen            │
│         │                                                              │
│         ▼                                                              │
│  StratonCoordinator  (DataUpdateCoordinator)                           │
│    • Polling-Fallback (Default 30 s), nur volatile Endpunkte           │
│    • Konsolidierter Gerätezustand als Single Source of Truth           │
│    • Push-Eingang aus dem Socket-Client                                │
│         │                        ▲                                     │
│         ▼                        │                                     │
│  StratonApiClient  ◄──Cookies──►  StratonSocketClient                  │
│    aiohttp + CookieJar             python-socketio (async)             │
│    Auto-Re-Login bei 401           emit: color-preview / color-change  │
│         │                        │                                     │
│         └────────┬───────────────┘                                     │
│                  ▼                                                     │
│         light · sensor · binary_sensor · number · switch · button      │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │  HTTP + WebSocket (lokal, unverschlüsselt)
                                   ▼
                    ATI Straton Flex G2 153 · 192.168.1.42
```

### 4.2 `StratonApiClient` — HTTP-Schicht

- `aiohttp.ClientSession` mit eigenem `CookieJar` für `connect.sid`.
- `async_login()`: `POST /login`, Content-Type `application/x-www-form-urlencoded`,
  Felder `username` / `password`. **Kein JSON** — das Frontend nutzt ein natives Form.
  Erfolg = `302` mit `Location: /` und `Set-Cookie`. Misserfolg = `302` auf `/login`.
- **Re-Login-Automatik** bei `401` oder Redirect auf `/login`: einmalig neu anmelden,
  Request wiederholen; danach `ConfigEntryAuthFailed` → HA-Reauth-Flow.
- `asyncio.Lock` serialisiert Logins gegen parallele Requests.
- **`/api/user` wird niemals aufgerufen** — siehe Sicherheitsbefund §9.

### 4.3 `StratonSocketClient` — Push und Steuerung

- `python-socketio[asyncio_client]`, verbindet nach erfolgreichem Login und übernimmt
  den `connect.sid`-Cookie des API-Clients.
- **Eingehend** (Server → Client):

| Event | Nutzlast | Verarbeitung |
|-------|----------|--------------|
| `temperature-spots` | `[{ externalId, temperature, rawtemperature, online }]` | Direktes Zustands-Update ohne HTTP |
| `changed-intensity` | unbestätigt | Update + Debug-Log |
| `intensity-auto-correction` | unbestätigt (vermutlich Temperatur-Derating) | Update + Info-Log |
| `new-spots` | — | `async_request_refresh()` (Vollreload) |
| `logout` | — | Session verwerfen, Re-Login |

- **Ausgehend** (Client → Server): `color-preview`, `color-change` — siehe Pfad A.
- Reconnect mit exponentiellem Backoff; nach Reconnect `GET /api/time` als
  Session-Probe (Muster aus dem Original-Frontend).
- **Degradiert sauber:** Fällt der Socket aus, bleibt die Integration über Polling
  voll lesefähig. Nur Pfad-A-Steuerung ist dann nicht verfügbar; Pfad B (REST) springt ein.

### 4.4 `StratonCoordinator` — Zustandsverwaltung

- Erstabruf lädt den statischen Bestand (`info`, `version`, `channels`, `colors`,
  `timelines`, `spots`, `par-table`, `presettings`, `timezone`).
- Polling holt nur die volatilen Endpunkte: **`/api/status`** (Kanalwerte,
  Preview-Flags), `/api/current`, `/api/timeinfo`. Das erfüllt NFR-02.
- `/api/current` nur wenn `info.adc === true`.
- `async_write_data()` kapselt Read-Modify-Write (Pfad C) mit `asyncio.Lock` und
  Snapshot-Backup; übernimmt die Serverantwort (`lines` → `timelines`) als neuen Zustand.

### 4.5 Entitätsmodell

| Plattform | Entität | Quelle | Prio |
|-----------|---------|--------|------|
| `light` | Leuchte gesamt — Helligkeit + RGB-nahe Kanalsteuerung | Pfad A | Muss |
| `number` | Kanalwert 0–255, **je Kanal** (W, V, RB, B, LC, R) → 6 Entitäten | Pfad A, `/api/status` | Muss |
| `switch` | Preview-/Manuell-Modus aktiv | `color-preview`, `status.isColorPreview` | Muss |
| `sensor` | Temperatur je Spot (3×, `°C`) | `temperature-spots`, `/api/temperatures` | Muss |
| `binary_sensor` | Spot online/offline (3×, `CONNECTIVITY`) | `temperature-spots.online` | Muss |
| `sensor` | Stromaufnahme `adc` + Prozent von `max` | `/api/current` | Soll |
| `binary_sensor` | Stromwarnung / Gefahr | `/api/current` → `isWarning`, `isDanger` | Soll |
| `sensor` | Aktueller Kanalwert je Kanal (6×, schreibgeschützt) | `/api/status` → `channels[].value` | Soll |
| `sensor` | WLAN-Signalstärke | `/api/wifistatus` | Kann |
| `sensor` | Uptime | `/api/uptime` | Kann |
| `select` | Farbpreset (10 Spektren) | `/api/colors`, Pfad B | Soll |
| `number` | Demo-Intensität / -Dauer | Pfad B | Soll |
| `button` | Gerätezeit mit HA synchronisieren | `POST /api/time` | Kann |
| `diagnostics` | Gerätezustand, **redigiert** | alle außer `/api/user` | Soll |

Alle Entitäten hängen an **einem** `DeviceInfo` (§2.1).

### 4.6 Abbildung der `light`-Entität

HA-`light`-Semantik passt nicht unmittelbar auf ein zeitplangesteuertes Gerät.
Festlegung:

- **`turn_on(brightness)`** → `color-preview {enabled:true}`, dann `color-change`
  je Kanal mit dem nach §4.7 skalierten und geklemmten Wert.
- **`turn_off()`** → alle Kanäle auf `0` (Preview-Modus bleibt aktiv).
- **Rückkehr zum Automatikbetrieb** ist *kein* `light`-Zustand, sondern der
  Preview-Switch bzw. ein Service `ati_straton.resume_schedule`
  (→ `color-preview {enabled:false}`).
- `is_on` leitet sich aus `/api/status.channels[].value > 0` ab.
- **Kanalgrenzen sind zwingend zu beachten** — siehe §4.7.

### 4.7 Sicherheitsmodell: Kanalgrenzen ⚠️ *sicherheitskritisch*

> **Grundannahme (Worst Case, bewusst gesetzt):** Das Gerät übernimmt per
> `color-change` gesendete Werte **ungeprüft**. Es findet **keine** geräteseitige
> Begrenzung statt. Jeder Schutz muss in der Integration liegen.

#### Warum das nicht trivial ist

`color-change` sendet Werte in der **Preset-Domäne 0–255** (verifiziert: das
Frontend übergibt `activeColor.values[].value` unverändert an das Event; die
UI-Validierung lautet `value < 0 || value > 255` → ungültig).

Es existieren jedoch **zwei unterschiedliche Zahlenräume**, deren Verhältnis
zueinander **nicht verifiziert** ist:

| Kanal | `spot.channels[].max` (Hardware) | max. Wert der Geräte-Farbpresets |
|-------|--------------------------------:|--------------------------------:|
| W | 116 | 255 |
| V | 49 | 200 |
| RB | 57 | 255 |
| B | 118 | 255 |
| LC | 59 | 255 |
| **R** | **15** | 64 |

Erschwerend: Die Presets werden im Normalbetrieb **immer** über die
Timeline-Intensität skaliert — die Tageskurve erreicht derzeit maximal **65 %**
(`nodes[].value`, Bereich 0–65). Ob der Preview-Modus diese Skalierung anwendet
oder den Rohwert direkt ausgibt, ist **unbekannt**.

Ein naiver `color-change` mit `value: 255` auf Kanal **R** läge damit potenziell
um **Faktor 17** über der Hardwaregrenze.

#### Vierstufiger Schutz

Alle Stufen greifen **unmittelbar vor `socket.emit`**, nicht in der Entität —
damit kein Codepfad (Service-Call, Automation, Szene, Reconnect-Replay) sie umgehen kann.

| Stufe | Regel | Verhalten bei Verletzung |
|-------|-------|--------------------------|
| **S0 — Typ/Bereich** | `value` muss `int` sein, `0 ≤ value ≤ 255`; `name` muss ein bekannter Kanal sein; `spots` müssen bekannte `externalId` sein | `ValueError`, **kein** Emit |
| **S1 — Kanaldeckel** | `ceiling[ch] = min( spot.channels[].max über alle Spots , max. Preset-Wert des Kanals )` | Wert wird geklemmt, Warnung ins Log |
| **S2 — Fehlendes `max`** | Deckel = Minimum der **bekannten** `max`-Werte des Kanalnamens. Existiert für einen Namen **kein einziger** `max`-Wert, gilt `ceiling = 0` | Emit wird verweigert |
| **S3 — Globaler Faktor** | `safety_factor` aus dem Options Flow, Default `1.0`, Bereich `0.1–1.0` | multiplikativ auf `ceiling` |

**Resultierende Default-Deckel** (S1, aus den Gerätedaten abgeleitet — die
jeweils konservativere der beiden Domänen):

```
W → 116    V → 49    RB → 57    B → 118    LC → 59    R → 15
```

**Fail-closed-Prinzip:** Lassen sich die Grenzen für einen Kanal nicht eindeutig
bestimmen (fehlende, widersprüchliche oder unvollständige Gerätedaten), gilt
`ceiling = 0` und der Schreibvorgang wird mit klarer Fehlermeldung **abgelehnt** —
niemals „im Zweifel durchlassen".

**Konkreter Anlass für S2:** Jeder der drei Spots besitzt einen **vierten
R-Kanal** (`channel: 4`) **ohne `max`-Feld** — verifiziert an allen drei Spots.
Würde ein fehlendes `max` als „unbegrenzt" interpretiert, wäre dieser Kanal schutzlos.

`color-change` adressiert Kanäle über den **Namen**, nicht über die physische
Kanalnummer — ein `{name: "R"}` trifft also beide R-Kanäle. Der Deckel für „R" ist
daher das Minimum der bekannten Werte (**15**). Das ist strikt konservativer als der
Normalbetrieb des Geräts, dessen eigene Presets denselben namensbasierten Befehl mit
Werten bis **64** senden. Ein vollständiges Deaktivieren von „R" wäre ein
Funktionsverlust ohne Sicherheitsgewinn und unterbleibt deshalb.

#### Skalierung der `light`-Helligkeit

Da die Deckel je Kanal stark differieren (15 … 118), darf HA-`brightness 0–255`
**nicht** 1:1 durchgereicht werden. Stattdessen:

```
device_value[ch] = round( brightness / 255 × ceiling[ch] × safety_factor )
```

Damit entspricht `brightness = 255` dem jeweiligen Kanaldeckel, nicht dem Rohwert
255. Die Abbildung ist verlustbehaftet (bei R nur 16 Stufen) und wird als solche
dokumentiert.

> **Anhebung der Deckel** ist ausschließlich über den Options Flow möglich,
> erfordert eine explizite Bestätigung und ist mit einem Warnhinweis versehen.
> Ohne empirische Klärung von O5 bleibt der konservative Default bestehen.

---

### 4.8 Temperaturwächter

Regelt die Intensität hysteresegesteuert herunter, wenn die gemeldete
Spot-Temperatur eine einstellbare Schwelle überschreitet, und gibt sie
schrittweise wieder frei.

```
       Reduktion
           ▲
    100 %  ┤                    ┌────────  REDUCING (T ≥ max_temp)
           │              ┌─────┘
           │        ┌─────┘      ····      HOLDING  (low < T < max)
           │  ┌─────┘                      Hysteresebereich, Pegel bleibt
      0 %  ┼──┘           └─────┐          RECOVERING (T ≤ low_temp)
           └──────────────────────────►    IDLE bei Pegel 0 → Zeitplan zurück
```

**Bedienelemente** (Entitäten, nicht Options Flow — dadurch im Dashboard
sichtbar und aus Automationen heraus änderbar):

| Entität | Bedeutung | Bereich |
|---------|-----------|---------|
| `switch` Temperaturwächter | Regelung ein/aus | — |
| `number` Abregeltemperatur | ab hier wird abgesenkt | 25–60 °C |
| `number` Freigabetemperatur | darunter wird freigegeben | 25–60 °C |
| `number` Reduktionsschritt | Absenkung je Schritt | 1–50 % |
| `sensor` Wächter-Zustand | `disabled`/`idle`/`reducing`/`holding`/`recovering` | — |
| `sensor` Wächter-Reduktion | aktuelle Absenkung | 0–100 % |

Einstellungen überdauern einen Neustart über `RestoreEntity`/`RestoreNumber` —
bewusst **nicht** über den Options Flow, weil jede Slider-Bewegung sonst einen
Reload der Integration auslösen würde. Ohne früheren Zustand startet der Wächter
**deaktiviert**.

**Regelverhalten**

- Auswertung bei **jedem** `temperature-spots`-Event (~2 s) für schnelle Reaktion.
- Regelschritte höchstens alle `step_interval` Sekunden (Konstante, 60 s). Ohne
  diese Sperre wäre die volle Absenkung in Sekunden erreicht und die Regelung
  würde schwingen.
- Eingangsgröße ist das **Maximum** über alle Spots, nicht der Mittelwert.

**Sicherheitseigenschaften**

| Eigenschaft | Umsetzung |
|-------------|-----------|
| Kann nie aufhellen | Schreibt nur Werte ≤ Snapshot der Ausgangswerte. Damit **unabhängig von O5a gefahrlos** |
| Kein Eingriff bei erloschener Leuchte | Sind alle Kanäle 0, wird nicht eingegriffen |
| Messausfall | Bestehende Absenkung bleibt bestehen (sichere Richtung), eine neue wird nicht begonnen |
| Ungültige Parameter | Wächter bleibt untätig, eine bestehende Absenkung wird **gehalten** statt schlagartig freigegeben |
| Hysterese erzwungen | `max_temp − low_temp ≥ 1 K`; die Entitäten führen die jeweils andere Schwelle nach, statt eine ungültige Konfiguration entstehen zu lassen |
| Verbindungsverlust | Gerät bleibt im abgesenkten Zustand — die kühlere und damit sichere Richtung |
| Entladen / HA-Stopp | Preview-Modus wird zwingend verlassen (NFR-10) |

> **Einschränkung (bewusst in Kauf genommen):** Solange der Wächter regelt, ist
> der **Tagesverlauf pausiert** — die Geräte-API kennt keine Möglichkeit, die
> Intensität abzusenken, ohne den Zeitplan zu übersteuern. Der Wächter arbeitet
> deshalb auf einem Snapshot der Kanalwerte zum Zeitpunkt des Eingriffs. Sobald
> die Absenkung 0 erreicht, wird der Preview-Modus verlassen und der Zeitplan
> übernimmt wieder.
>
> Für eine spätere Ausbaustufe denkbar: die Tageskurve (16 Knoten) lokal
> interpolieren und die Absenkung auf den jeweils aktuellen Sollwert anwenden.
> Das ersetzt die Pause, bringt aber das Risiko, dass die eigene Interpolation
> vom Gerät abweicht.

> **Kollisionsgefahr:** Das Gerät besitzt eine eigene Temperaturgrenze
> (`info.maxTemperature`, ab Werk **60 °C**) und meldet ein Ereignis
> `intensity-auto-correction`. Der Wächter sollte **unterhalb** dieser Grenze
> eingestellt werden, damit sich beide Regelungen nicht überlagern. Das Ereignis
> wird auf Info-Level protokolliert, um eine Überlagerung erkennbar zu machen.

### 4.9 Intensitätssteuerung ✅ *der maßgebliche Steuerpfad*

Am Gerät verifiziert am 31.07.2026, bestätigt sowohl durch Strommessung als auch
durch direkte Beobachtung der Leuchte.

#### Modell

Der Intensitätsregler der Geräteoberfläche skaliert **alle** Kurvenknoten relativ
zu ihrem unveränderlichen Originalwert und speichert anschließend:

```
node.value = node.valueOrg × n / maxValueOrg          n = Reglerstellung 0…100
PUT /api/data { timelines, spots, colors }
```

`valueOrg` bleibt dabei unangetastet und ist der stabile Anker — deshalb ist die
Operation verlustfrei umkehrbar.

Belegt durch einen Playwright-Mitschnitt der originalen Oberfläche: Bei den
Reglerstellungen 33, 49 und 71 sendete sie exakt die Werte, die
`intensity.scaled_timelines()` reproduziert. Diese Werte sind als Testfälle
hinterlegt.

#### Gemessene Wirkung

| Intensität | Stromaufnahme |
|-----------:|--------------:|
| 60,0 | 490 ADC |
| 30,0 | 294 ADC |
| 15,0 | 166 ADC |

Die Wirkung tritt **innerhalb von drei Sekunden** ein. Der Zusammenhang ist
annähernd linear mit einem Grundverbrauch von rund 85 ADC bei Intensität 0.

#### Zwei Schreibvarianten

| Funktion | Verwendung | Verhalten |
|----------|-----------|-----------|
| `scaled_timelines(tl, n)` | `number.Intensität` | Regler-Semantik des Geräts, normalisiert alle Knoten auf die Formel |
| `rescaled_by_factor(tl, f)` | Temperaturwächter | Skaliert die **Ist-Werte**, normalisiert nichts |

Die Unterscheidung ist notwendig: Am Testgerät existierten Knoten mit
`value == valueOrg`, während alle übrigen bei Verhältnis 0,8125 lagen. Die
Regler-Formel würde diese Ausreißer stillschweigend mitziehen. Der Wächter
arbeitet deshalb auf einem exakten Schnappschuss und schreibt ihn bei der
Freigabe wortgetreu zurück — beides am Gerät als exakt bestätigt.

#### Schutzmaßnahmen

| Maßnahme | Umsetzung |
|----------|-----------|
| Wertebereich | `0 ≤ n ≤ 100`, Typprüfung, `bool` ausgeschlossen |
| Obergrenze | `max_intensity` aus dem Options Flow klemmt **jede** Änderung |
| Backup | Vor jedem Schreibvorgang wird der Vorzustand über HAs `Store` persistiert |
| Absturzsicherung | Beim Start wird eine unterbrochene Wächter-Absenkung erkannt und zurückgenommen |
| Rundung | Drei Nachkommastellen, passend zur Speichergenauigkeit des Geräts (z. B. 63.375). Mit zwei Stellen wäre schon Faktor 1,0 kein No-op |
| Flash-Schonung | Regelschritte des Wächters höchstens alle **5 Minuten** — jeder Schritt ist ein Flash-Schreibvorgang |
| Wechselseitiger Ausschluss | Während der Wächter regelt, ist `number.Intensität` nicht bedienbar |

> **Verbleibendes Risiko:** `PUT /api/data` ersetzt das **gesamte** Dokument und
> kennt kein ETag. Parallele Änderungen über die Weboberfläche gehen verloren
> (O9). Das ist unvermeidbar — die Geräte-API bietet nichts Granulareres.

## 5. Funktionale Anforderungen

| ID | Anforderung | Prio |
|----|-------------|------|
| **FR-01** | Config Flow mit Host/Benutzer/Passwort; Verbindungs- und Anmeldetest vor Anlegen | Muss |
| **FR-02** | Reauth-Flow bei ungültigen Zugangsdaten | Muss |
| **FR-03** | Options Flow: Polling-Intervall, Opt-in für Zeitplan-Schreiben (Pfad C) | Soll |
| **FR-04** | Lesedaten aus §6 als Entitäten/Attribute abgebildet | Muss |
| **FR-05** | Temperatur und Online-Status je Spot per Push | Muss |
| **FR-06** | Polling-Fallback bei Socket-Ausfall ohne Entitätsverlust | Muss |
| **FR-07** | Kanalsteuerung über Pfad A (`color-preview`/`color-change`) | Muss |
| **FR-08** | Service `resume_schedule` zur Rückkehr in den Automatikbetrieb | Muss |
| **FR-09** | **Vierstufiger Kanalschutz nach §4.7 (S0–S3), fail-closed, unmittelbar vor `socket.emit`.** Kein Codepfad darf ihn umgehen | **Muss (kritisch)** |
| **FR-10** | Farbpreset-Auswahl und Demo-Modus über Pfad B | Soll |
| **FR-11** | Timelines lesbar als Entitätsattribute (Tageskurve, 16 Knoten) | Soll |
| **FR-12** | Zeitplan-Schreiben (Pfad C) nur mit Opt-in **und** automatischem Snapshot-Backup vor jedem `PUT` | Kann |
| **FR-13** | Service zur Gerätezeit-Synchronisation (`POST /api/time`) | Kann |
| **FR-14** | `diagnostics` mit Redaktion von Zugangsdaten **und** `/api/user` | Soll |
| **FR-15** | Temperaturhistorie (`/api/temperatures`, 596 Samples) beim Start optional importieren | Kann |
| **FR-16** | **Temperaturwächter** nach §4.8: hysteresegeregelte Absenkung der Intensität | Muss |
| **FR-17** | Wächter über `switch` schaltbar, Schwellen und Reduktionsschritt über `number`-Slider einstellbar | Muss |
| **FR-18** | Wächter-Einstellungen überdauern einen Neustart; ohne früheren Zustand startet er deaktiviert | Muss |
| **FR-19** | Wächter-Zustand und aktuelle Absenkung als Sensoren beobachtbar | Soll |
| **FR-20** | Drosselung der Push-Weitergabe an HA; Regeleingriffe werden davon unberührt sofort durchgereicht | Muss |
| **FR-21** | HACS-Paketierung: `hacs.json`, `README.md`, Repository-Struktur | Soll |
| **FR-22** | **Globale Intensität** als `number`-Slider (0–100 %) nach §4.9 | Muss |
| **FR-23** | Obergrenze `max_intensity` klemmt jede Änderung über die Integration | Muss |
| **FR-24** | Backup des Vorzustands vor **jedem** Schreibvorgang, persistent über `Store` | Muss |
| **FR-25** | Beim Start wird eine durch Absturz unterbrochene Wächter-Absenkung zurückgenommen | Muss |
| **FR-26** | Keine externen Python-Abhängigkeiten — eigener Engine.IO-3-Client auf `aiohttp` | Soll |

---

## 6. Schnittstellenvertrag

### 6.1 Authentifizierung

```http
POST /login HTTP/1.1
Content-Type: application/x-www-form-urlencoded

username=<user>&password=<pass>
```

**Verifiziert:** Erfolg → `302`, `Location: /`,
`Set-Cookie: connect.sid=s%3A…; Path=/; HttpOnly`.
Ungültige Session erkennbar an: HTTP `401`, `302` auf `/login`, oder Socket-Event `logout`.

### 6.2 Endpunktübersicht (alle verifiziert, `200` mit gültiger Session)

**Zustand und Telemetrie — von der Integration genutzt**

| Methode | Pfad | Antwort (verifiziert) |
|---------|------|----------------------|
| `GET` | `/api/state` | `{ initialized: true }` — Health-Check |
| `GET` | `/api/info` | `{ id, adc, showAllTemperatures, maxTemperature, isMaster, deviceType, deviceMessages }` |
| `GET` | `/api/version` | `{ number: "3.0.4", date, release: "2024.4" }` |
| `GET` | `/api/status` | `{ channels[6], channelsPrevious[6], isColorPreview, isTimePreview, isResetPreview, isTestPreview, previewTimeStamp, previewTimeStampChanged, isDangerCurrent, _resetState }` |
| `GET` | `/api/channels` | `[{ id, label, value, name, sort, factor, valueTemperature? }]` (6 Kanäle) |
| `GET` | `/api/current` | `{ adc: 583, max: 1173, warn: 1120, isWarning: false, isDanger: false }` |
| `GET` | `/api/temperatures` | `[{ ts, data: [{ t: 39.7, i: "10000001:0", o: 1 }] }]` — 596 Samples |
| `GET` | `/api/spots` | `[{ _id, name, active, enabled, channels[] }]` (3 Spots) |
| `GET` | `/api/timelines` | `[{ _id, name, visible, active, linecolor, colorEditable, presetting, spotAddresses, spots[], nodes[16] }]` |
| `GET` | `/api/colors` | `[{ _id, name, values[] }]` (10 Farbpresets) |
| `GET` | `/api/presettings` | `[{ _id, filename, title, description, group, disabled, timerange, intensities[] }]` (7) |
| `GET` | `/api/par-table` | `[{ label: "30cm", factor: 3 }, …]` (4 Distanzen) |
| `GET` | `/api/timeinfo` | `{ ts, offset: -120, timezone, systime, tsWithTz, initialized }` |
| `GET` | `/api/uptime` | `{ uptime, days, hours, minutes, seconds }` |
| `GET` | `/api/wifistatus` | `{ signalStrength, channel, connected }` |
| `GET` | `/api/hostname` | `{ hostname: "ATI-Straton-10000001" }` |
| `GET` | `/api/devices` | `[]` — Slave-Geräte |
| `GET` | `/api/demo` | `null` (inaktiv) oder Demo-Modell |

**Schreibend — von der Integration genutzt**

| Methode | Pfad | Payload |
|---------|------|---------|
| `POST` | `/api/demo` | `{ color, sections[], intensity, duration, active, start }` (Pfad B) |
| `POST` | `/api/time` | `{ ts: <epoch ms>, offset: <getTimezoneOffset()> }` |
| `PUT` | `/api/data` | `{ timelines, spots, colors }` → `{ lines, spots, colors }` (Pfad C, Opt-in) |

**Vorhanden, aber bewusst NICHT genutzt** *(außerhalb des Scopes, §1.2)*

`GET /api/time` (liefert nackten ISO-String, kein JSON) · `GET|POST /api/user`
(**Sicherheitsbefund §9**) · `POST /api/network` · `POST /api/timezone` ·
`POST /api/reset-device` · `GET /api/reboot` · `POST /api/delete-spot` ·
`POST /api/indicate-spots` · `PUT /api/timeline` · `GET /api/wifis` ·
`POST /api/setup` · `GET /api/check-online-firmware-update` ·
`GET /api/download-online-firmware-update/` · `GET /firmware-update/` ·
`GET /api/create-support-file` · `POST /api/start-up-time` ·
`POST /api/ignoreHelpDialogs` · `POST /load-presettings`

> `reset-device`, `reboot`, `delete-spot` und die Firmware-Endpunkte sind destruktiv
> bzw. betriebsunterbrechend. Sie werden **nicht** als HA-Entitäten exponiert, um
> versehentliche Auslösung durch Automationen auszuschließen.

### 6.3 Fehlerbehandlung

| Situation | Reaktion |
|-----------|----------|
| `401` / Redirect auf `/login` | Einmaliger Re-Login + Retry; danach `ConfigEntryAuthFailed` |
| Timeout / `ClientError` | `UpdateFailed` → Entitäten `unavailable`, nächster Zyklus erneut |
| `5xx` bei `PUT /api/data` | Kein lokaler Zustandswechsel, Backup bleibt erhalten |
| Ungültiges JSON | `UpdateFailed`, Rohtext auf Debug-Level |
| Socket-Abriss | Backoff-Reconnect; Polling übernimmt, Pfad B ersetzt Pfad A |

### 6.4 Einheiten und Wertebereiche (verifiziert)

| Größe | Gerät | Home Assistant |
|-------|-------|----------------|
| Kanalwert | `0–255` (Preset-Domäne), **je Kanal gedeckelt** (§4.7) | `brightness 0–255` → skaliert auf `ceiling[ch]`, **nicht 1:1** |
| Temperatur | `t` in `°C`, eine Nachkommastelle (z. B. `39.7`) | `SensorDeviceClass.TEMPERATURE` |
| Strom | `adc` roh, mit `max: 1173` und `warn: 1120` | Rohwert **und** `adc/max` in % |
| Zeit (Knoten) | Sekunden seit Mitternacht (`0…86400`) | `datetime.time` |
| Zeit (API) | Epoch-ms + `offset` in Minuten (`-120` = UTC+2) | UTC-`datetime` |
| Spot-Referenz | `externalId` = `"<deviceId>:<spot._id>"` | Entity-Unique-ID-Bestandteil |
| PAR | `factor` je Distanz (30 cm → 3.0 … 75 cm → 1.25) | berechneter Sensor |

---

## 7. Nicht-funktionale Anforderungen

| ID | Anforderung |
|----|-------------|
| **NFR-01** | Vollständig asynchron; keine blockierenden Aufrufe im Event Loop |
| **NFR-02** | Grundlast ≤ 3 HTTP-Requests pro Intervall (`/api/status`, `/api/current`, `/api/timeinfo`), Default 30 s |
| **NFR-03** | Nicht erreichbares Gerät → `unavailable`, keine Exception-Spam im Log |
| **NFR-04** | Passwörter nur im HA-Credential-Store; nie in Logs oder Diagnostics |
| **NFR-05** | HACS-konforme Struktur `custom_components/ati_straton/` |
| **NFR-06** | Durchgängige Typannotationen; `ruff` + `mypy` sauber |
| **NFR-07** | Unit-Tests gegen Mock-Server auf Basis der Phase-0-Fixtures; **keine** Testabhängigkeit von echter Hardware |
| **NFR-08** | Schreiboperationen idempotent oder explizit als nicht-idempotent dokumentiert |
| **NFR-09** | Gerät bietet **kein TLS**. Als Einschränkung dokumentieren; Integration erzwingt kein HTTPS |
| **NFR-10** | Preview-Modus wird beim Entladen der Integration deterministisch beendet (`color-preview {enabled:false}`), damit das Aquarium nie in einem Override hängen bleibt |

**NFR-10 ist sicherheitskritisch für den Tierbestand** und gehört in `async_unload_entry`
sowie in einen `homeassistant_stop`-Listener.

---

## 8. Umsetzungsplan

### ✅ Phase 0 — Reverse Engineering *(abgeschlossen 2026-07-31)*

Alle Endpunkte erfasst, Payloads als Fixtures gesichert, Steuerpfade identifiziert,
O1–O4 geschlossen.

### ✅ Phase 1 — Lesende Integration *(abgeschlossen 2026-07-31)*

`StratonApiClient` (Login, Re-Login, Endpunktsperre) · `StratonCoordinator`
(Polling, Grenzenableitung) · `sensor`, `binary_sensor` · Config Flow + Reauth +
Options Flow · Diagnostics · **Sicherheitslayer `limits.py` inkl. 40 Tests**
→ *erfüllt Z1, Z6, Z7 · FR-01 bis FR-04, FR-09, FR-14*

Gegen das reale Gerät verifiziert: Anmeldung, Cookie-Handling, Endpunktsperre,
Ableitung der Kanaldeckel aus Live-Daten, 16 resultierende Entitäten.

> **Vorgezogen:** FR-09 (Sicherheitslayer) war für Phase 3 geplant, wurde aber
> vollständig in Phase 1 umgesetzt. Damit existiert der Schutz, **bevor** der erste
> Schreibpfad gebaut wird — es gibt zu keinem Zeitpunkt ungeschützten Code.

### ✅ Phase 2 — Push und Temperaturwächter *(abgeschlossen 2026-07-31)*

`StratonSocketClient` (Event-Mapping, Reconnect-Backoff, gedrosselte Weitergabe) ·
`TemperatureGuardian` inkl. 24 Tests · `switch`, `number`, Wächter-Sensoren ·
HACS-Paketierung
→ *erfüllt Z3 · FR-05, FR-06, FR-16 bis FR-21*

Gegen das reale Gerät verifiziert: Socket-Verbindung mit Session-Cookie,
`temperature-spots` im Zwei-Sekunden-Takt, vollständige Regelkette im Trockenlauf
(Schrittsperre, Hysterese, Faktorberechnung) — **ohne** jeden Sendevorgang.

### Phase 3 — Steuerung

Pfad A (`color-preview`/`color-change`) · `light`, `number`, `switch` ·
Klemmung auf `channel.max` · `resume_schedule` · NFR-10
→ *erfüllt Z2 · FR-07, FR-08, FR-09*

### Phase 4 — Komfort und Feinschliff

Pfad B (Demo, Farbpresets, `select`) · Timelines als Attribute ·
optionaler Pfad C mit Backup · Diagnostics · Übersetzungen · HACS-Paketierung
→ *erfüllt Z4, Z5 · FR-10 bis FR-15*

---

## 9. Umgang mit sensiblen Endpunkten

Zwei Endpunkte der Geräte-API liefern Zugangs- beziehungsweise
Netzwerkkonfigurationsdaten aus. Sie werden von der Integration **nie**
aufgerufen: Der HTTP-Client weist sie hart ab (`FORBIDDEN_ENDPOINTS`), sie
erscheinen nicht in den Diagnosedaten und werden nicht als Fixture abgelegt.

Ebenso werden `reset-device`, `reboot` und `delete-spot` bewusst nicht als
Entitäten angeboten. Sie sind betriebsunterbrechend beziehungsweise destruktiv,
und keine Automation soll sie versehentlich auslösen können.

Unabhängig davon: Das Gerät bietet kein TLS. Zugangsdaten gehen im Klartext über
das lokale Netz, weshalb ein eigenes IoT-VLAN oder -WLAN zu empfehlen ist und am
Gerät kein anderweitig genutztes Passwort stehen sollte.


## 10. Verbleibende offene Punkte

| ID | Punkt | Auswirkung | Auflösung |
|----|-------|-----------|-----------|
| ~~O1~~ | ~~Zugangsdaten fehlen~~ | — | ✅ **geschlossen** in Phase 0 |
| ~~O2~~ | ~~Demo-Schreibpfad unbekannt~~ | — | ✅ **geschlossen**: `POST /api/demo` + Socket-Pfad A |
| ~~O3~~ | ~~Zweck von `/api/status`~~ | — | ✅ **geschlossen**: Live-Kanalzustand + Preview-Flags |
| ~~O4~~ | ~~Skalierung von Temperatur/ADC~~ | — | ✅ **geschlossen**: `t` in °C; `adc` mit `max`/`warn` |
| ~~O5~~ | ~~Klemmt das Gerät `color-change` selbst?~~ | — | ✅ **geschlossen per Entscheidung**: Es wird angenommen, dass das Gerät **nicht** klemmt. Schutz vollständig in der Integration (§4.7, FR-09) |
| ~~O5a~~ | ~~Wendet der Preview-Modus die Timeline-Intensität an?~~ | — | ✅ **hinfällig**: Der Preview-Pfad ist am Gerät wirkungslos. Gesteuert wird über §4.9 |
| ~~O13~~ | ~~Pausiert Preview den Tagesverlauf?~~ | — | ✅ **hinfällig**, siehe O5a |
| **O14** | Ist `/api/status.channels` überhaupt je befüllt, oder nur im (funktionslosen) Preview-Betrieb? | Nur kosmetisch — die Kanal-Sensoren wurden entfernt | Gering priorisiert |
| **O15** | Wie viele Schreibzyklen verträgt der Flash des Geräts? | Bestimmt das minimale Regelintervall des Wächters | Herstellerangabe fehlt; Default 5 min konservativ gewählt |
| **O16** | `limits.py` schützt einen Pfad, den es am Gerät nicht gibt | Toter Code mit 40 Tests | Bleibt als Reserve erhalten, falls ein Kanalschreibweg gefunden wird — siehe §11 |
| ~~O6a~~ | ~~Nutzlast von `temperature-spots`~~ | — | ✅ **geschlossen**: `[{externalId, temperature, rawtemperature: [{value, addr}], online}]`, Takt ~2 s. `rawtemperature` ist eine **Liste je Messadresse**, kein Skalar |
| **O6b** | Nutzlast von `changed-intensity` und `intensity-auto-correction` | Erkennung einer Überlagerung mit der geräteeigenen Regelung | Beide Events blieben in 115 s Mitschnitt stumm (Leuchte auf 0). Erneut mitschneiden, wenn die Leuchte den Tagesverlauf fährt |
| **O13** | Pausiert der Preview-Modus den Tagesverlauf tatsächlich, und setzt das Gerät ihn beim Verlassen korrekt fort? | Bestimmt, wie lang der Wächter eingreifen darf | Empirisch mit dem ersten echten Sendevorgang (§11) |
| **O7** | Semantik von `demo.duration` — Timeout oder Fade-Dauer? | Zustandsmodell Pfad B | Phase 4, empirisch |
| **O8** | Bedeutung von `isTimePreview` / `previewTimeStamp` (Zeitpunkt-Vorschau der Kurve?) | Zusatzfunktion möglich | Phase 4, optional |
| **O9** | Kein Versions-/ETag-Feld → **Lost Updates** bei paralleler Weboberflächen-Nutzung | Datenverlust im Tagesverlauf | Mitigation: Pfad C nur mit Opt-in + Backup (FR-12) |
| **O10** | Verhalten bei mehreren gleichzeitigen Sessions (verdrängt ein neuer Login die alte?) | Konflikt HA ↔ Browser | Phase 1, empirisch prüfen |
| **O11** | Firmware-Abhängigkeit: Assets tragen `?v=1734354894262`, FW 3.0.4 | Update kann API brechen | `/api/version` protokollieren; defensive Payload-Auswertung |
| **O12** | `/api/temperatures` liefert 596 Samples — Ringpuffer-Größe und Sample-Intervall unbekannt | Import-Strategie FR-15 | Phase 4 |

---

## 11. Stand der Umsetzung

```
StratonIntegration/
├── hacs.json · README.md · .gitignore
├── custom_components/ati_straton/
│   ├── limits.py         Sicherheitslayer S0-S3, fail-closed  ← sicherheitskritisch
│   ├── guardian.py       Temperaturwächter, reine Zustandslogik
│   ├── api.py            HTTP-Client, Re-Login, Endpunktsperre
│   ├── socket_client.py  Socket.IO-Push und Preview-Steuerpfad
│   ├── coordinator.py    Zustand, Grenzenableitung, Wächteranwendung
│   ├── config_flow.py    Einrichtung, Reauth, Options Flow
│   ├── entity.py         gemeinsame DeviceInfo-Basis
│   ├── sensor.py         Strom, Temperaturen, Kanalwerte, Wächter
│   ├── binary_sensor.py  Spot-Online, Stromwarnung, Manueller Modus
│   ├── switch.py         Wächter ein/aus
│   ├── number.py         Wächter-Schwellen als Slider
│   ├── diagnostics.py    redigierte Diagnosedaten
│   └── const.py · manifest.json · strings.json
└── tests/                64 Tests, Fixtures aus Phase 0 (ohne user/network)
```

### ✅ Phase 3 — Intensitätssteuerung *(abgeschlossen 2026-07-31)*

`intensity.py` mit 25 Tests gegen den Playwright-Mitschnitt der Original-
oberfläche · `number.Intensität` · Wächter auf `PUT /api/data` umgestellt ·
Backup und Absturzsicherung über `Store` · eigener Engine.IO-3-Client, damit die
Integration **keine** externen Abhängigkeiten mehr braucht
→ *erfüllt Z2, Z5 · FR-22 bis FR-26*

Am Gerät verifiziert, visuell und per Strommessung bestätigt.

### Historischer Hinweis: der Irrweg über den Socket

Phase 2 hatte die Steuerung auf `color-preview`/`color-change` aufgebaut. Drei
Fehlschläge in Folge führten erst zur richtigen Diagnose:

1. `python-socketio` 5.x spricht Engine.IO 4, das Gerät Engine.IO 3 — der
   Handshake passte nicht, das Gerät antwortete mit `logout`.
2. Auch mit korrektem Protokoll blieben die Ereignisse **wirkungslos**.
3. Der erste `PUT`-Test wirkte scheinbar ebenfalls nicht — tatsächlich war die
   Amplitude zu klein gewählt (Faktor 0,8) **und die Stromaufnahme gar nicht
   gemessen worden**. Der Pfad hatte von Anfang an funktioniert.

Lehre für weitere Arbeiten an diesem Gerät: **Ohne `/api/current` als
objektives Messmittel ist keine Aussage über die Wirksamkeit eines Befehls
belastbar.** Das bloße Zurücklesen von `/api/timelines` beweist nur, dass etwas
gespeichert wurde — nicht, dass es wirkt.

### Der frühere kritische Punkt (erledigt)

Bislang wurde **noch nie etwas an das Gerät gesendet** — alle Verifikationen
waren lesend oder Trockenläufe. Der erste echte `color-preview`/`color-change`
verändert die Leuchte und sollte **gemeinsam und beobachtet** erfolgen, weil er
drei offene Punkte auf einmal berührt:

- **O5a** — wendet der Preview-Modus die Timeline-Intensität an, oder gibt er
  Rohwerte aus? Nur mit Strommessung (`/api/current`, aktuell 558/1173)
  entscheidbar. Davon hängt ab, ob die konservativen Deckel angehoben werden dürfen.
- **O13** — pausiert Preview den Tagesverlauf, und setzt das Gerät ihn beim
  Verlassen sauber fort?
- **O6b** — feuert dabei `intensity-auto-correction`, überlagert sich also die
  geräteeigene Regelung mit dem Wächter?

Bis dahin ist der Wächter zwar vollständig implementiert und getestet, seine
Wirkung auf das Gerät aber noch nicht praktisch bestätigt.

**Danach Phase 3:** `light`-Entität, Farbpreset-Auswahl, Demo-Modus (Pfad B).
