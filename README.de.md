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

---

## Inhalt

- [Funktionsumfang](#funktionsumfang)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Nutzung](#nutzung)
- [Wie Schreibvorgänge abgesichert sind](#wie-schreibvorgänge-abgesichert-sind)
- [Risiken und Warnhinweise](#risiken-und-warnhinweise)
- [Sicherheitsbefunde am Gerät](#sicherheitsbefunde-am-gerät)
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
    option: Bewährte Einstellungen der Community · Programm B
```

### Farbzusammensetzung ändern

Nicht angegebene Kanäle bleiben unverändert. Der Wertebereich ist 0 bis 255.

```yaml
service: ati_straton.set_color
data:
  device_id: <deine Straton>
  color: Farbe D
  values:
    B: 255
    V: 180
    LC: 120
```

Die verfügbaren Farben und ihre aktuelle Zusammensetzung stehen in den
Attributen des Sensors **Farben**.

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

## Sicherheitsbefunde am Gerät

Diese betreffen die ATI Straton selbst, nicht diese Integration. Sie fielen beim
Reverse Engineering der API an und sind dokumentiert, damit das Risiko
einschätzbar ist.

| # | Befund |
|---|---|
| **S-01** | `GET /api/user` liefert den **interne Kontodaten** des Kontos (ungeschützt) an jede angemeldete Sitzung |
| **S-02** | **Kein TLS.** Zugangsdaten gehen bei jedem Login im Klartext über das Netz |
| **S-03** | `reset-device`, `reboot` und `delete-spot` sind mit einer normalen Sitzung auslösbar, ohne zusätzliche Bestätigung |
| **S-04** | `GET /api/network` liefert den **Netzwerkangaben des geräteeigenen Access Points** im Klartext |

Diese Integration ruft `/api/user` und `/api/network` **nie** auf; beide sind im
HTTP-Client hart gesperrt und aus den Diagnosedaten ausgeschlossen. Die
destruktiven Endpunkte werden bewusst nicht als Entitäten angeboten, damit keine
Automation sie versehentlich auslöst.

Empfehlung: am Gerät kein Passwort verwenden, das anderswo im Einsatz ist, und
die Leuchte in ein eigenes IoT-VLAN oder -WLAN stellen.

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

86 Tests laufen gegen aufgezeichnete Gerätedaten in `tests/fixtures/` und
benötigen **keine** Hardware. Die Erwartungswerte für Intensitätsskalierung und
Programmladen stammen aus einem Verkehrsmitschnitt der Originaloberfläche, die
Integration bildet das Geräteverhalten also Wert für Wert nach.

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
keinen Support dafür. „ATI" und „Straton" sind Marken des Herstellers und werden
hier ausschließlich beschreibend verwendet.

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

Fehlerberichte und Beiträge: <https://github.com/benkrebs/ATIStraton/issues>
