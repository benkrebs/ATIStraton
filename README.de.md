# ATI Straton — Home Assistant Integration

**Repository:** <https://github.com/benkrebs/ATIStraton>

[English version → README.md](README.md)

Lokale Home-Assistant-Integration für **ATI Straton** Aquarien-LED-Leuchten. Ohne
Cloud, ohne Hersteller-App — die Integration spricht direkt mit dem Gerät im
eigenen Netz.

Entwickelt und verifiziert gegen eine **Straton Flex G2 153** mit Firmware
**3.0.4**.

> [!WARNING]
> **Dies ist ein privates Hobbyprojekt. Es hat nichts mit ATI Aquaristik zu tun
> — weder unterstützt noch geprüft noch genehmigt.** Die Geräte-API ist nicht
> dokumentiert und wurde aus der Weboberfläche der Leuchte rekonstruiert. Sie
> kann sich mit jedem Firmware-Update ohne Vorwarnung ändern.
>
> **Der Einsatz kann die Leuchte beschädigen, den Tagesverlauf zerstören oder
> dem Tierbestand schaden, der davon abhängt. Die Nutzung erfolgt vollständig
> auf eigene Gefahr.** Bitte vor der Installation
> [Risiken und Warnhinweise](#risiken-und-warnhinweise) lesen.
> Der Code ist überwiegend KI-generiert. Hilfe und Verbesserungen sind sehr
> willkommen.

---

## Inhalt

- [Funktionsumfang](#funktionsumfang)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Nutzung](#nutzung)
- [Alle Entitäten](#alle-entitäten)
- [Farben ansehen und bearbeiten](#farben-ansehen-und-bearbeiten)
- [Wie Schreibvorgänge abgesichert sind](#wie-schreibvorgänge-abgesichert-sind)
- [Risiken und Warnhinweise](#risiken-und-warnhinweise)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Entwicklung](#entwicklung)
- [Haftungsausschluss](#haftungsausschluss)
- [Lizenz](#lizenz)

---

## Funktionsumfang

### Überwachung

- Temperatur je LED-Modul, per Push aktualisiert (das Gerät meldet alle ~2 s)
- Online-Status je LED-Modul
- Stromaufnahme und Auslastung in Prozent, inklusive der geräteeigenen Warn- und
  Gefahrenschwelle
- Betriebsmodus — siehe [Betriebsmodus](#betriebsmodus)

### Ein- und Ausschalten

Ein `switch` **Beleuchtung** setzt die Intensität auf 0 und stellt beim
Einschalten den zuletzt aktiven Wert wieder her. Der Tagesverlauf bleibt dabei
erhalten. Einen echten Netzschalter kennt das Gerät nicht.

### Intensitätssteuerung

Ein Schieberegler (`number`) setzt die **globale Intensität** von 0 bis 100 %,
genau wie der Intensitätsregler der Geräteoberfläche. Die Änderung greift
innerhalb weniger Sekunden.

Die **Form der Tageskurve bleibt erhalten**: Jeder Stützpunkt wird relativ zu
seinem unveränderlichen Originalwert skaliert, es ändert sich also nur die
Gesamthelligkeit. Am Testgerät gemessen: Intensität 60 → 490 ADC, 30 → 294 ADC,
15 → 166 ADC.

### Lichtprogramme

Auswahl eines der Lichtprogramme des Geräts, zusammen mit einer
**Gewöhnungsstufe**. Beides liegt als `select`-Entität vor.

| Stufe | Für | Intensität |
|---|---|---|
| Eingewöhnungsphase | lichtempfindliche Korallen | 30 % |
| Lichtgewöhnt | lichtgewöhnte Korallen | 60 % |
| Starklichtgewöhnt | stark lichtgewöhnte Korallen | 90 % |

Die Gewöhnungsstufe wirkt beim nächsten Programmwechsel — genauso wie in der
Geräteoberfläche, wo sie Teil des Ladevorgangs ist. Das bestehende Lichtfenster
(Anfangs- und Endzeit) bleibt erhalten und wird nicht vom Programmvorschlag
überschrieben.

### Farben

Alle Farbvoreinstellungen werden mit ihrer vollständigen spektralen
Zusammensetzung aufgelistet. Bearbeiten lässt sie sich über den Dienst
`ati_straton.set_color`.

### Temperaturwächter

Senkt die Intensität automatisch ab, wenn die Leuchte zu warm wird, und gibt sie
schrittweise wieder frei, sobald sie abkühlt. Vollständig über die Oberfläche
einstellbar:

| Bedienelement | Bedeutung |
|---|---|
| `switch` **Temperaturwächter** | schaltet die Regelung ein und aus |
| `number` **Abregeltemperatur** | ab diesem Wert wird abgesenkt |
| `number` **Freigabetemperatur** | darunter wird die Absenkung zurückgenommen |
| `number` **Reduktionsschritt** | Absenkung je Regelschritt in Prozent |

Zwischen den beiden Schwellen bleibt die Absenkung stehen. Diese Hysterese
verhindert, dass die Regelung an der Schwelle taktet. Regelschritte erfolgen
**höchstens alle 5 Minuten** — die Begründung steht unter
[Risiken und Warnhinweise](#risiken-und-warnhinweise).

Zwei Sensoren machen die Regelung nachvollziehbar: **Wächter-Zustand**
(`idle` · `reducing` · `holding` · `recovering` · `disabled`) und
**Wächter-Reduktion** in Prozent.

### Betriebsmodus

Ein Sensor zeigt, wer gerade den Tagesverlauf bestimmt:

| Zustand | Bedeutung |
|---|---|
| `normal` | Das Gerät fährt seinen Zeitplan, unberührt von dieser Integration |
| `manual_intensity` | Die Intensität wurde über Home Assistant gesetzt |
| `guard` | Der Temperaturwächter hält den Verlauf abgesenkt |

Der Wächter hat Vorrang: Solange er eingreift, sind Intensitätsregler und
Programmauswahl gesperrt.

---

## Voraussetzungen

- Home Assistant 2025.1 oder neuer
- Eine ATI Straton, erreichbar im lokalen Netz
- Benutzername und Passwort der Geräte-Weboberfläche

Es gibt **keine externen Python-Abhängigkeiten**. Das Gerät spricht Socket.IO 2.x
(Engine.IO 3); dafür bringt die Integration einen eigenen schlanken Client auf
Basis von `aiohttp` mit, das Home Assistant ohnehin enthält. Ein Pin auf eine
alte `python-socketio`-Version hätte andere Integrationen in derselben
Python-Umgebung gefährden können.

## Installation

### Über HACS

1. HACS → Integrationen → ⋮ → **Benutzerdefinierte Repositories**
2. `https://github.com/benkrebs/ATIStraton` als URL eintragen, Kategorie **Integration**
3. „ATI Straton" installieren, Home Assistant neu starten

### Manuell

Den Ordner `custom_components/ati_straton/` in das eigene
`config/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → ATI Straton**

| Feld | Beispiel |
|---|---|
| Host oder IP-Adresse | `192.168.1.42` |
| Benutzername | Benutzer der Geräte-Weboberfläche |
| Passwort | zugehöriges Passwort |

Die Zugangsdaten liegen im verschlüsselten Credential-Store von Home Assistant.
Sie werden nie in Logs oder Diagnosedaten geschrieben.

Unter **Konfigurieren** lassen sich anschließend einstellen:

| Option | Vorgabe | Bedeutung |
|---|---|---|
| Abfrageintervall | 30 s | wie oft nicht gepushte Werte aktualisiert werden |
| **Maximale Intensität** | 100 % | harte Obergrenze — klemmt *jede* Änderung über diese Integration |

Im Zweifel die **maximale Intensität** auf den Wert senken, mit dem das Becken
normalerweise läuft. Das ist der einfachste Schutz gegen eine fehlerhafte
Automation.

## Nutzung

### Intensität setzen

```yaml
service: number.set_value
target:
  entity_id: number.ati_straton_intensitat
data:
  value: 45
```

### Programm mit Gewöhnungsstufe laden

Erst die Stufe setzen, dann das Programm — die Stufe wird beim Laden gelesen.

```yaml
- service: select.select_option
  target:
    entity_id: select.ati_straton_gewohnungsstufe
  data:
    option: Eingewöhnungsphase

- service: select.select_option
  target:
    entity_id: select.ati_straton_lichtprogramm
  data:
    option: Bewährte Einstellungen der Community · Programmname
```

### Farbzusammensetzung ändern

Nicht angegebene Kanäle bleiben unverändert. Der Wertebereich ist 0 bis 255.

```yaml
service: ati_straton.set_color
data:
  device_id: <deine Straton>
  color: <Name aus dem Sensor Farben>
  values:
    B: 255
    V: 180
    LC: 120
```

Die verfügbaren Farben und ihre aktuelle Zusammensetzung stehen in den
Attributen des Sensors **Farben**.

#### Farbkanäle

> [!IMPORTANT]
> Als Schlüssel unter `values` gilt der **Kanalcode**, nicht die Beschriftung
> aus der Geräteoberfläche. Beide weichen an einer Stelle voneinander ab:
> Kanal **`LC`** wird in der Oberfläche als **`C`** angezeigt. Im Service muss
> `LC` stehen.

Welche Kanäle deine Leuchte besitzt, hängt vom Modell ab. Nachsehen kannst du im
Attribut `composition` des Sensors **Farben** — dort stehen genau die Schlüssel,
die auch der Service akzeptiert.

Die Firmware 3.0.4 kennt diese Kanalcodes:

| Code | Anzeige | Vermutete Bedeutung |
|---|---|---|
| `W` | W | Weiß |
| `WW` | WW | Warmweiß |
| `CW` | CW | Kaltweiß |
| `HW` | HW | Weiß, weitere Variante |
| `B` | B | Blau |
| `RB` | RB | Royalblau |
| `RB-V` | RB-V | Royalblau/Violett gemischt |
| `V` | V | Violett |
| `UV` | UV | Ultraviolett |
| `LC` | **C** | Cyan |
| `R` | R | Rot |
| `T5` | T5 | T5-Leuchtstoffkanal der Hybrid-Modelle |

Die Spalte **Anzeige** ist aus der Sprachdatei des Geräts ausgelesen und damit
gesichert. Die **Bedeutung** hinterlegt die Firmware nirgends — sie ist aus der
üblichen Benennung in der Aquaristik erschlossen und kann bei einzelnen Modellen
abweichen.

Auf dem Testgerät (Straton Flex G2 153) sind sechs davon vorhanden. Von oben
nach unten, so wie die Regler in der Geräteoberfläche stehen:

| Regler in der Oberfläche | Schlüssel für den Service |
|---|---|
| V | `V` |
| RB | `RB` |
| B | `B` |
| **C** | **`LC`** |
| W | `W` |
| R | `R` |

Die Reihenfolge ergibt sich aus dem Feld `sort` des Geräts und ist nicht
alphabetisch.

Daneben kennt die Firmware die Langformen `blue`, `green`, `royalblue`,
`violett`, `violett405`, `violett425` und `white`. Sie tauchen in den Farbdaten
des Testgeräts nicht auf und sind als Schlüssel vermutlich nicht verwendbar.

### Temperaturwächter aktivieren

```yaml
- service: number.set_value
  target: { entity_id: number.ati_straton_wachter_abregeltemperatur }
  data: { value: 48 }

- service: number.set_value
  target: { entity_id: number.ati_straton_wachter_freigabetemperatur }
  data: { value: 44 }

- service: switch.turn_on
  target: { entity_id: switch.ati_straton_temperaturwachter }
```

Die Abregeltemperatur sollte **unterhalb** der geräteeigenen Grenze liegen (ab
Werk 60 °C), sonst arbeiten zwei Regelungen gegeneinander.

---

## Alle Entitäten

Die Integration legt **ein** Gerät an, unter dem alles hängt. Die genauen
Entitäts-IDs richten sich nach dem Namen, den du dem Gerät gibst.

### Steuern

| Entität | Typ | Bedeutung |
|---|---|---|
| **Beleuchtung** | `switch` | Aus setzt die Intensität auf 0, Ein stellt den zuletzt aktiven Wert wieder her. Der Tagesverlauf bleibt dabei erhalten, er wird nur auf null skaliert. Das Gerät kennt keinen echten Netzschalter. |
| **Intensität** | `number` | Globale Helligkeit 0–100 %. Wirkt wie der Regler der Geräteoberfläche und behält die Form der Tageskurve. |
| **Lichtprogramm** | `select` | Lädt ein Programm. **Überschreibt den Tagesverlauf.** Attribute enthalten die Programmliste, die verwendeten Farben und den kompletten Verlauf. |
| **Gewöhnungsstufe** | `select` | Eingewöhnungsphase, Lichtgewöhnt oder Starklichtgewöhnt. Wirkt erst beim nächsten Programmwechsel. |
| **Temperaturwächter** | `switch` | Schaltet die automatische Absenkung bei Hitze ein und aus. |
| **Wächter Abregeltemperatur** | `number` | Ab diesem Wert wird abgesenkt. |
| **Wächter Freigabetemperatur** | `number` | Darunter wird die Absenkung zurückgenommen. |
| **Wächter Reduktionsschritt** | `number` | Absenkung je Regelschritt in Prozent. |
| **Farbe bearbeiten** | `select` | Welche der zehn Farben die Kanalregler bearbeiten. |
| **Kanal 1 Violett (V)** … **Kanal 6 Rot (R)** | `number` | Anteil des jeweiligen Kanals, 0–255. Jeder Regler trägt einen Punkt in seiner Kanalfarbe. Schreibt in einen Puffer, **nicht** direkt zum Gerät. |
| **Farbe speichern** | `button` | Überträgt den Puffer. Nur verfügbar, wenn es etwas zu speichern gibt. |
| **Änderungen verwerfen** | `button` | Lädt die Gerätewerte zurück. |

Beleuchtung, Intensität und Lichtprogramm sind **gesperrt, solange der Wächter
regelt** — sonst würden zwei Schreiber um denselben Tagesverlauf konkurrieren.

### Beobachten

| Entität | Typ | Bedeutung |
|---|---|---|
| **Aktuelle Intensität** | `sensor` | Was der Tagesverlauf **gerade jetzt** vorgibt, interpoliert auf die Gerätezeit. |
| **Betriebsmodus** | `sensor` | `normal`, `manual_intensity` oder `guard` — wer gerade den Tagesverlauf bestimmt. |
| **Spot_SiriusPro 1–3** | `sensor` | Temperatur des jeweiligen LED-Moduls in °C. |
| **Spot_SiriusPro 1–3 Verbindung** | `binary_sensor` | Ob das Modul antwortet. |
| **Stromaufnahme (Rohwert)** | `sensor` | ADC-Rohwert des Geräts, ohne Einheit. |
| **Stromauslastung** | `sensor` | Derselbe Wert als Prozentsatz der Gerätegrenze. **Keine Helligkeitsangabe** — siehe unten. |
| **Stromwarnung** | `binary_sensor` | Warn- oder Gefahrenschwelle des Geräts überschritten. |
| **Wächter-Zustand** | `sensor` | `disabled`, `idle`, `reducing`, `holding` oder `recovering`. |
| **Wächter-Reduktion** | `sensor` | Aktuelle Absenkung in Prozent. |
| **Farben** | `sensor` | Anzahl der Farben; die Zusammensetzungen stehen in den Attributen. |

### Drei Zahlen, die leicht zu verwechseln sind

| Entität | Was sie zeigt | Beispiel um 11:11 Uhr |
|---|---|---|
| `number` **Intensität** | die Reglerstellung, also die **Tagesspitze** | 70,0 |
| `sensor` **Aktuelle Intensität** | was die Kurve **jetzt** vorgibt | 64,45 |
| `sensor` **Stromauslastung** | gemessene Stromaufnahme gegen die Gerätegrenze | 54,4 % |

Die **Stromauslastung ist keine Helligkeit.** Selbst bei Intensität 100 läge sie
nur bei rund 66 %, und bei Intensität 0 bleiben etwa 6 % Grundlast für die
Elektronik. Sie hängt zudem vom Spektrum ab: Farben mit viel Weiß ziehen mehr
Strom als rein blaue. Als Kennzahl taugt sie zur Überwachung der Hardware, nicht
zur Beurteilung der Helligkeit.

### Was ein „Spot" ist

Die Straton ist **eine** Leuchte, in der mehrere LED-Module stecken. Das Gerät
nennt sie *Spots*; *SiriusPro* ist die Modellbezeichnung dieser Module. Es sind
keine getrennten Leuchten — die Integration legt sie unter demselben Gerät an.

Beim Testgerät (153 cm) sind es drei Module, die je zwei DMX-Adressen belegen:

| Modul | Adressen | Beispieltemperatur |
|---|---|---|
| Spot_SiriusPro 1 | 1 + 2 | 40,8 °C |
| Spot_SiriusPro 2 | 3 + 4 | 39,7 °C |
| Spot_SiriusPro 3 | 5 + 6 | 38,9 °C |

Jedes Modul hat einen **eigenen Temperaturfühler** und meldet seine Verbindung
getrennt — daher dreimal Temperatur und dreimal Verbindung. Kürzere oder längere
Modelle haben entsprechend weniger oder mehr Module.

Dass ein Modul dauerhaft wärmer ist als die anderen, ist normal und hängt von
seiner Position in der Leuchte ab. Der **Temperaturwächter bewertet immer das
heißeste Modul**, nicht den Mittelwert.

### Wenn die Push-Verbindung abreißt

Die Temperaturen kommen ausschließlich über den Push-Kanal — die Historie unter
`/api/temperatures` ist mit rund 78 kB zu schwer, um sie im Polling-Takt
abzurufen. Reißt der Kanal ab, weil das Gerät neu startet, das WLAN aussetzt
oder die Session serverseitig endet, geschieht Folgendes:

| | |
|---|---|
| **Erkennung** | Kommt länger als die vom Gerät gemeldete Frist (60 s) kein einziges Frame, gilt die Verbindung als tot. Das erfasst auch halb offene Verbindungen, die für das Betriebssystem weiter „offen“ aussehen |
| **Wiederaufbau** | Die Verbindung wird selbsttätig neu aufgebaut, mit wachsender Wartezeit von 5 s bis 5 min und einer **frischen Anmeldung**, falls das Gerät die Session verworfen hat |
| **Kennzeichnung** | Sind die Messwerte älter als zwei Minuten, werden Temperatur- und Verbindungssensoren **unverfügbar**, statt einen eingefrorenen Wert weiter als gültige Temperatur auszuweisen |
| **Wächter** | Ohne aktuelle Messung regelt der Temperaturwächter nicht weiter. Eine bestehende Absenkung bleibt bestehen, eine neue wird nicht begonnen — die sichere Richtung |

Der Rest der Integration — Intensität, Programme, Farben — läuft davon
unberührt über HTTP weiter. Der Zustand des Kanals steht in den
Diagnosedaten unter `push`.

---

## Farben ansehen und bearbeiten

### Bearbeiten ohne YAML

1. **Farbe bearbeiten** auf die gewünschte Farbe stellen
2. Die sechs Kanalregler ziehen
3. **Farbe speichern** drücken

Die Regler schreiben bewusst erst in einen Puffer. Das ist kein Umweg, sondern
Hardwareschutz: Jeder Schreibvorgang ist ein Flash-Zugriff auf die Leuchte, und
eine einzige Reglerbewegung erzeugte sonst Dutzende davon. Derselbe Ablauf wie
in der Geräteoberfläche, wo man ebenfalls anpasst und dann speichert.

Ein Wechsel der Farbe verwirft einen offenen Puffer. **Farbe speichern** und
**Änderungen verwerfen** sind nur verfügbar, solange es etwas zu speichern gibt.

Die Kanalregler heißen ausgeschrieben und tragen **den Kanalcode in Klammern** —
etwa *Cyan (LC)*. Das ist Absicht: `ati_straton.set_color` erwartet den Code als
Schlüssel, und ausgerechnet dieser Kanal heißt in der Geräteoberfläche `C`.

Vor jedem Regler steht ein **Punkt in der Kanalfarbe**. Home Assistant kann
Entitäts-Icons nicht einfärben, deshalb liefert die Integration dafür ein
kleines Bild unter `/api/ati_straton/channel/<Code>` aus und hinterlegt es als
`entity_picture`. Eine eigene Lovelace-Karte ist dafür nicht nötig.

Zusätzlich führt jede dieser Entitäten im Attribut `hex` ihre Farbe — als
Vorbereitung für eine spätere Karte, die auch die gemischten Farben darstellt.

#### Warum die Regler eine Ziffer tragen

Home Assistant sortiert die Entitäten auf der Geräteseite **alphabetisch nach
Namen** und bietet keine Einstellung dafür. Ohne Ziffer stünde *Blau* vor
*Violett* und *Rot* vor *Royalblau*. Die vorangestellte Position erzwingt die
sinnvolle Reihenfolge:

```
Farbe bearbeiten
Kanal 1 Violett (V)
Kanal 2 Blau (B)
Kanal 3 Royalblau (RB)
Kanal 4 Cyan (LC)
Kanal 5 Weiß (W)
Kanal 6 Rot (R)
```

Auf einem **eigenen Dashboard** brauchst du das nicht — dort bestimmst du die
Reihenfolge selbst:

```yaml
type: entities
title: Farbe bearbeiten
entities:
  - entity: select.ati_straton_farbe_bearbeiten
  - entity: number.ati_straton_kanal_1_violett_v
  - entity: number.ati_straton_kanal_2_blau_b
  - entity: number.ati_straton_kanal_3_royalblau_rb
  - entity: number.ati_straton_kanal_4_cyan_lc
  - entity: number.ati_straton_kanal_5_weiss_w
  - entity: number.ati_straton_kanal_6_rot_r
  - entity: button.ati_straton_farbe_speichern
  - entity: button.ati_straton_anderungen_verwerfen
```

Die genauen Entitäts-IDs richten sich nach dem Namen deines Geräts.

### Anzeigen

Eine eigene Lovelace-Komponente brauchst du dafür **nicht**. Alle Angaben liegen
als Attribute vor und lassen sich mit einer Markdown-Karte darstellen.

### Alle Farben mit ihrer Zusammensetzung

```yaml
type: markdown
content: |
  {% set colors = state_attr('sensor.ati_straton_farben', 'colors') %}
  | Farbe | V | RB | B | C | W | R |
  |---|--:|--:|--:|--:|--:|--:|
  {% for c in colors -%}
  | {{ c.name }} | {{ c.composition.V }} | {{ c.composition.RB }} |
  {{- c.composition.B }} | {{ c.composition.LC }} | {{ c.composition.W }} |
  {{- c.composition.R }} |
  {% endfor %}
```

Die Spalte **C** trägt intern den Schlüssel `LC` — siehe
[Farbkanäle](#farbkanäle).

### Farben des gewählten Programms

Ein Programm nutzt selten alle Farben. Beim Testgerät sind es drei von zehn:

```yaml
type: markdown
content: |
  {% set p = 'select.ati_straton_lichtprogramm' %}
  ### {{ states(p) }}

  {% for c in state_attr(p, 'colors_in_use') -%}
  **{{ c.name }}** — {% for k, v in c.composition.items() %}{{ k }} {{ v }}{{ ", " if not loop.last }}{% endfor %}
  {% endfor %}
```

### Tagesverlauf mit Farbwechseln

```yaml
type: markdown
content: |
  | Uhrzeit | Intensität | Farbe |
  |---|--:|---|
  {% for e in state_attr('select.ati_straton_lichtprogramm', 'schedule') -%}
  | {{ e.time }} | {{ e.intensity }} | {{ e.color }} |
  {% endfor %}
```

Ergibt beim Testgerät eine Tabelle wie diese — morgens `Farbe E`, tagsüber
`eine Farbe`, abends zurück:

| Uhrzeit | Intensität | Farbe |
|---|--:|---|
| 09:00 | 0 | Farbe E |
| 10:00 | 52,71 | Farbe E |
| 12:00 | 70,0 | eine Farbe |
| 19:08 | 49,41 | eine Farbe |
| 20:11 | 49,41 | Farbe E |
| 22:30 | 0 | Farbe E |

---

## Wie Schreibvorgänge abgesichert sind

Jede Änderung durchläuft denselben abgesicherten Pfad:

| Ebene | Wirkung |
|---|---|
| **Bereichsprüfung** | Intensität `0…100`, Farbkanäle `0…255`, nur ganze Zahlen. Alles andere wird abgelehnt, bevor überhaupt ein Request gebaut wird |
| **Maximale Intensität** | Eine einstellbare Obergrenze klemmt *jede* Änderung über diese Integration |
| **Backup vor jedem Schreibvorgang** | Der Vorzustand wird über den `Store` von Home Assistant gesichert, bevor etwas gesendet wird |
| **Absturzsicherung** | Stirbt Home Assistant, während der Wächter regelt, wird die ursprüngliche Kurve beim nächsten Start wiederhergestellt |
| **Nur ein Schreiber** | Intensität und Programmauswahl sind gesperrt, solange der Wächter aktiv ist |
| **Exakte Rücksetzung** | Der Wächter arbeitet auf einem wortgetreuen Schnappschuss und schreibt ihn unverändert zurück, statt aus einer Formel neu zu rechnen |

Der letzte Punkt ist wesentlich: Die geräteeigene Intensitätsformel normalisiert
jeden Stützpunkt, und reale Kurven können Punkte enthalten, die davon abweichen.
Ein Neuberechnen würde diese stillschweigend verändern — der Wächter tut das
deshalb nie.

---

## Risiken und Warnhinweise

> [!CAUTION]
> **Eine Aquarienbeleuchtung ist lebenserhaltende Technik.** Korallen und
> anderer Besatz hängen von einem stabilen Lichtregime ab. Ein falscher Wert,
> ein fehlgeschlagener Schreibvorgang oder eine unbeaufsichtigte Automation
> können realen Schaden anrichten. Nichts an diesem Projekt wurde vom Hersteller
> geprüft oder freigegeben.

**Jeder Schreibvorgang ersetzt den kompletten Zeitplan.** Die Geräte-API kennt
nichts Granulareres als ein Vollersetzungs-`PUT`, und sie führt keine
Versionskennung. Wer gleichzeitig in der ATI-Oberfläche speichert, überschreibt
die jeweils andere Seite stillschweigend. Die Integration legt vor jeder
Änderung ein Backup an, den Konflikt erkennen kann sie aber nicht.

**Der Wächter verändert die Tageskurve wirklich.** Solange er eingreift, sind
die Kurvenwerte am Gerät tatsächlich abgesenkt — es handelt sich nicht um eine
vorübergehende Überlagerung. Bei der Freigabe wird der vorherige Zustand
wortgetreu zurückgeschrieben. Wird Home Assistant mitten im Eingriff beendet,
erkennt die Integration das beim nächsten Start und stellt die Kurve wieder her.
Startet sie nie wieder, **bleibt die Kurve abgesenkt**.

**Schreibvorgänge nutzen den Flash-Speicher des Geräts ab.** Jeder Regelschritt
des Wächters ist ein Flash-Schreibvorgang. Der Hersteller nennt keine
Lebensdauer, deshalb ist das Intervall mit 5 Minuten bewusst konservativ. Wer es
verkürzt, sollte diesen Zusammenhang kennen.

**Firmware-Updates können alles brechen.** Die API ist nicht dokumentiert. Ein
Update kann Felder umbenennen, Bedeutungen ändern oder Endpunkte entfernen. Nach
jedem Firmware-Update sollte man prüfen, ob die Integration sich noch wie
erwartet verhält, bevor man sich darauf verlässt.

**Erste Versuche mit Blick aufs Becken.** Beim Ausprobieren die Leuchte im Auge
behalten und die ATI-Oberfläche als Rückfallebene geöffnet lassen. Änderungen
dieser Integration lassen sich dort jederzeit rückgängig machen.

---

## Bekannte Einschränkungen

- **Keine Steuerung einzelner Farbkanäle.** Der dafür im Web-Frontend
  vorhandene Socket-Pfad (`color-preview` / `color-change`) ist am Gerät
  **wirkungslos** — verifiziert mit protokollkonformem Client. Steuerbar sind
  die Gesamtintensität und die gespeicherten Farbzusammensetzungen.
- **`/api/status.channels` ist nicht die Live-Ausgabe.** Dort stehen Nullen,
  während die Leuchte läuft. Die Kanalwert-Sensoren wurden deshalb entfernt.
- **Intensität und Programm sind gesperrt, solange der Wächter regelt**, damit
  nicht zwei Schreiber um den Zeitplan konkurrieren.
- **Eigene Programme bringen keinen Zeitbereich mit.** Die Integration leitet das
  Lichtfenster aus der bestehenden Kurve ab, damit ein Programmwechsel die
  gewohnten Zeiten nicht verschiebt.

---

## Entwicklung

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/python -m ruff check custom_components tests
```

143 Tests laufen gegen erfundene Testdaten in `tests/fixtures/` und benötigen
**keine** Hardware. Die Testdaten sind vollständig erfunden und enthalten keine
Herstellerdaten; siehe `tests/fixtures/README.md`.

`requirements.md` dokumentiert das gesamte Reverse Engineering: jeden Endpunkt,
das Datenmodell, die Messungen und die Sackgassen.

Für Skripte, die mit einem echten Gerät sprechen, `.env.example` nach `.env`
kopieren. Dort niemals ein Passwort eintragen — `.env.example` erklärt, wie man
stattdessen den Schlüsselbund des Systems nutzt. Sowohl `.env` als auch
`backups/` sind vom Repository ausgeschlossen.

---

## Haftungsausschluss

Dies ist ein **privates Hobbyprojekt**, entwickelt für eine einzelne Leuchte und
veröffentlicht für den Fall, dass es jemand anderem nützt.

**Es hat keinerlei Verbindung zur ATI Aquaristik GmbH & Co. KG.** Der Hersteller
hat es weder entwickelt noch geprüft, genehmigt oder unterstützt und bietet
keinen Support dafür.

„ATI", „Straton" und „SiriusPro" sind Marken des jeweiligen Inhabers. Sie
erscheinen hier **ausschließlich beschreibend**, um zu benennen, mit welchem
Gerät diese Software spricht; ein Anspruch darauf wird nicht erhoben. Der Name
des Repositories folgt der Gepflogenheit der Home-Assistant-Gemeinschaft,
Integrationen nach dem unterstützten Gerät zu benennen, und bedeutet keine
amtliche Herkunft.

Die Lizenz in [LICENSE](LICENSE) gilt für den Quellcode dieser Integration. Sie
erstreckt sich nicht auf API-Strukturen, Benennungen oder Gerätedaten, die vom
Hersteller stammen.

Die Geräte-API ist nicht öffentlich. Sie wurde aus der Weboberfläche der Leuchte
rekonstruiert und kann sich mit jedem Firmware-Update ändern oder wegfallen.

**Die Software wird ohne jede Gewährleistung bereitgestellt. Die Nutzung erfolgt
vollständig auf eigene Gefahr.** Weder der Autor noch Mitwirkende haften für
Schäden an der Hardware, den Verlust der Lichtkonfiguration, Schäden am
Tierbestand oder sonstige Folgen der Nutzung. Der Einsatz kann
Gewährleistungsansprüche gegenüber dem Hersteller gefährden.

Wer mit diesen Bedingungen nicht einverstanden ist, sollte die Integration nicht
installieren.

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).

Geltungsbereich, Marken und Interoperabilität: [NOTICE.md](NOTICE.md).

Fehlerberichte und Beiträge: <https://github.com/benkrebs/ATIStraton/issues>
