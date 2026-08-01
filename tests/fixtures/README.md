# Testdaten

Sämtliche Dateien in diesem Verzeichnis sind **frei erfunden**. Sie bilden
nur die Struktur der Geräte-API nach, damit die Tests ohne angeschlossene
Hardware laufen.

Die Werte ergeben aquaristisch bewusst keinen Sinn und stammen weder von
ATI noch aus einem realen Gerät. Namen wie `Testfarbe 3` und
`test.program.1.json` sind Platzhalter.

Die Kurvenwerte folgen `value = valueOrg × 50 / 80`, damit sich die
Erwartungswerte der Tests von Hand nachrechnen lassen. Zwei Knoten weichen
absichtlich davon ab (`value == valueOrg`) und bilden den Sonderfall ab,
den `rescaled_by_factor` berücksichtigen muss.
