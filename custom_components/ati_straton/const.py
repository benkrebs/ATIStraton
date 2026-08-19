"""Konstanten der ATI Straton Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ati_straton"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

CONF_MAX_INTENSITY: Final = "max_intensity"

STORAGE_VERSION: Final = 1

# Das Gerät sendet temperature-spots etwa alle zwei Sekunden. Ungedrosselt würde
# das die Zustandsmaschine und die Recorder-Datenbank fluten; Regeleingriffe des
# Wächters werden davon unabhängig sofort durchgereicht.
PUSH_THROTTLE_SECONDS: Final = 15.0

# Die Telemetrie kommt ausschließlich über den Push-Kanal. Bleibt sie länger als
# diese Spanne aus, gelten die Messwerte als veraltet: Die Spot-Sensoren werden
# unverfügbar, statt einen eingefrorenen Wert weiter als gültig auszuweisen, und
# der Temperaturwächter regelt nicht mehr auf einer alten Messung.
TELEMETRY_STALE_SECONDS: Final = 120.0

# Wartezeiten zwischen Wiederverbindungsversuchen des Push-Kanals.
SOCKET_RETRY_MIN_SECONDS: Final = 5.0
SOCKET_RETRY_MAX_SECONDS: Final = 300.0

# Ab dieser Standzeit gilt eine Verbindung als getragen und die Wartezeit wird
# zurückgesetzt. Ohne diese Schwelle würde ein Gerät, das die Verbindung sofort
# wieder fallen lässt, in einem Dauertakt von Neuversuchen enden.
SOCKET_STABLE_SECONDS: Final = 60.0

# Endpunkte, die einmalig beim Start gelesen werden (statischer Bestand).
STATIC_ENDPOINTS: Final[tuple[str, ...]] = (
    "info",
    "version",
    "hostname",
    "channels",
    "colors",
    "timelines",
    "spots",
    "par-table",
    "presettings",
    "timezone",
)

# Endpunkte, die im Polling-Intervall aktualisiert werden (volatiler Zustand).
VOLATILE_ENDPOINTS: Final[tuple[str, ...]] = ("status", "timeinfo")

# Socket.IO-Events (Gerät -> Integration).
EVENT_TEMPERATURE_SPOTS: Final = "temperature-spots"
EVENT_CHANGED_INTENSITY: Final = "changed-intensity"
EVENT_INTENSITY_AUTO_CORRECTION: Final = "intensity-auto-correction"
EVENT_NEW_SPOTS: Final = "new-spots"
EVENT_LOGOUT: Final = "logout"

# Socket.IO-Events (Integration -> Gerät).
EMIT_COLOR_PREVIEW: Final = "color-preview"
EMIT_COLOR_CHANGE: Final = "color-change"

# Endpunkte, die Zugangs- beziehungsweise Netzwerkkonfigurationsdaten
# ausliefern. Sie werden nie abgerufen und sind hier vermerkt, damit auch
# Diagnostik und Tests sie ausschließen.
FORBIDDEN_ENDPOINTS: Final[frozenset[str]] = frozenset({"user", "network"})
