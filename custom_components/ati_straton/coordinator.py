"""Zustandsverwaltung der ATI Straton Integration."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    StratonApiClient,
    StratonAuthError,
    StratonConnectionError,
    StratonError,
)
from .const import (
    DOMAIN,
    PUSH_THROTTLE_SECONDS,
    SOCKET_RETRY_MAX_SECONDS,
    SOCKET_RETRY_MIN_SECONDS,
    SOCKET_STABLE_SECONDS,
    STATIC_ENDPOINTS,
    STORAGE_VERSION,
    TELEMETRY_STALE_SECONDS,
    VOLATILE_ENDPOINTS,
)
from .guardian import GuardianConfig, GuardianDecision, TemperatureGuardian
from .intensity import (
    MAX_INTENSITY,
    MIN_INTENSITY,
    IntensityError,
    current_intensity,
    intensity_at,
    max_value_org,
    node_values,
    rescaled_by_factor,
    scaled_timelines,
)
from .programs import (
    ColorPreset,
    Program,
    build_load_payload,
    colors_in_schedule,
    derive_timerange,
    parse_colors,
    parse_programs,
    schedule_overview,
    with_updated_color,
)
from .socket_client import StratonSocketClient

_LOGGER = logging.getLogger(__name__)


class StratonMode(StrEnum):
    """Betriebsmodus der Integration gegenüber dem Gerät.

    Beschreibt, **wer** gerade den Tagesverlauf bestimmt.
    """

    NORMAL = "normal"
    """Das Gerät fährt seinen Zeitplan; die Integration hat ihn nicht verändert."""

    MANUAL_INTENSITY = "manual_intensity"
    """Die Intensität wurde über die Integration gesetzt."""

    GUARD = "guard"
    """Der Temperaturwächter hält den Tagesverlauf abgesenkt."""


@dataclass
class SpotReading:
    """Live-Telemetrie eines physischen LED-Moduls.

    ``rawtemperature`` liefert das Gerät als Liste je Messadresse, etwa
    ``[{"value": 40.0, "addr": 1}, {"value": 40.7, "addr": 2}]``. ``temperature``
    ist der daraus gebildete Repräsentativwert.
    """

    external_id: str
    temperature: float | None = None
    raw_temperature: list[dict[str, Any]] = field(default_factory=list)
    online: bool | None = None


@dataclass
class StratonData:
    """Konsolidierter Gerätezustand."""

    info: dict[str, Any] = field(default_factory=dict)
    version: dict[str, Any] = field(default_factory=dict)
    hostname: dict[str, Any] = field(default_factory=dict)
    channels: list[dict[str, Any]] = field(default_factory=list)
    colors: list[dict[str, Any]] = field(default_factory=list)
    timelines: list[dict[str, Any]] = field(default_factory=list)
    spots: list[dict[str, Any]] = field(default_factory=list)
    presettings: list[dict[str, Any]] = field(default_factory=list)
    par_table: list[dict[str, Any]] = field(default_factory=list)
    status: dict[str, Any] = field(default_factory=dict)
    current: dict[str, Any] = field(default_factory=dict)
    timeinfo: dict[str, Any] = field(default_factory=dict)
    readings: dict[str, SpotReading] = field(default_factory=dict)
    # Monotone Zeitbasis der letzten Telemetriemeldung. Ohne sie wäre nicht
    # unterscheidbar, ob ein Messwert aktuell ist oder nur der letzte vor einem
    # Verbindungsabriss.
    readings_at: float | None = None
    # Geräteübersetzung (lang/lang-de_DE.json), löst Schlüssel wie
    # PRESETTING_TITLE_8_1 in lesbare Namen auf.
    translations: dict[str, str] = field(default_factory=dict)

    @property
    def device_id(self) -> str:
        return str(self.info.get("id") or self.hostname.get("hostname") or "unknown")

    @property
    def has_adc(self) -> bool:
        return bool(self.info.get("adc"))

    @property
    def preview_active(self) -> bool:
        return bool(self.status.get("isColorPreview"))

    @property
    def telemetry_age(self) -> float | None:
        """Alter der letzten Temperaturmeldung in Sekunden."""
        if self.readings_at is None:
            return None
        return max(0.0, time.monotonic() - self.readings_at)

    @property
    def telemetry_stale(self) -> bool:
        """True, wenn die Telemetrie zu alt ist, um noch als gültig zu gelten."""
        age = self.telemetry_age
        return age is None or age > TELEMETRY_STALE_SECONDS

    @property
    def max_temperature(self) -> float | None:
        """Höchste aktuell gemeldete Spot-Temperatur — Eingang des Wächters.

        Bei veralteter Telemetrie ``None``: Der Wächter darf nicht auf einer
        eingefrorenen Messung weiterregeln. ``None`` lässt ihn eine bestehende
        Absenkung halten und keine neue beginnen — die sichere Richtung.
        """
        if self.telemetry_stale:
            return None
        values = [
            reading.temperature
            for reading in self.readings.values()
            if isinstance(reading.temperature, (int, float))
        ]
        return max(values) if values else None

    @property
    def device_max_temperature(self) -> float | None:
        """Vom Gerät selbst gesetzte Temperaturgrenze (``info.maxTemperature``)."""
        value = self.info.get("maxTemperature")
        return float(value) if isinstance(value, (int, float)) else None


class StratonCoordinator(DataUpdateCoordinator[StratonData]):
    """Pollt das Gerät und nimmt Push-Updates des Socket-Clients entgegen."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: StratonApiClient,
        scan_interval: int,
        max_intensity: float = MAX_INTENSITY,
        guardian_config: GuardianConfig | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.client = client
        self._static_loaded = False
        self.data = StratonData()

        self.socket: StratonSocketClient | None = None
        self._socket_task: asyncio.Task[None] | None = None
        self._socket_closing = False
        # Erzwingt vor dem nächsten Verbindungsaufbau eine neue Anmeldung —
        # gesetzt, wenn das Gerät die Session beendet hat oder ein Versuch
        # gescheitert ist.
        self._relogin_needed = False
        self.guardian = TemperatureGuardian(guardian_config)
        self._max_intensity = max_intensity
        # Exakter Stand vor dem Eingriff des Wächters; dient zugleich als
        # Merkmal dafür, dass eine Absenkung aktiv ist.
        self._guard_snapshot: list[dict[str, Any]] | None = None
        # Wird gesetzt, sobald die Intensität über die Integration verändert
        # wurde, und beim Laden eines Programms wieder zurückgenommen.
        self._manual_intensity = False
        # Intensität vor dem Ausschalten, damit das Einschalten sie
        # wiederherstellen kann.
        self._intensity_before_off: float | None = None
        # Farb-Editor: ausgewählte Farbe und ungespeicherter Bearbeitungsstand.
        self._color_edit_id: int | None = None
        self._color_buffer: dict[str, int] = {}
        # Sprachdatei des Geräts passend zur Home-Assistant-Sprache.
        self._language = "de_DE" if hass.config.language.startswith("de") else "en_US"
        self._write_lock = asyncio.Lock()
        self._backup_store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.backup"
        )
        self._last_push = 0.0

    @property
    def guard_engaged(self) -> bool:
        """True, solange der Wächter den Tagesverlauf abgesenkt hält."""
        return self._guard_snapshot is not None

    @property
    def mode(self) -> StratonMode:
        """Wer bestimmt gerade den Tagesverlauf.

        Der Wächter hat Vorrang: Greift er ein, ist das der maßgebliche Zustand,
        auch wenn zuvor eine Intensität von Hand gesetzt wurde.
        """
        if self._guard_snapshot is not None:
            return StratonMode.GUARD
        if self._manual_intensity:
            return StratonMode.MANUAL_INTENSITY
        return StratonMode.NORMAL

    @property
    def max_intensity(self) -> float:
        return self._max_intensity

    # ---------------------------------------------------------------- Polling

    async def _async_update_data(self) -> StratonData:
        data = self.data if self._static_loaded else StratonData()
        try:
            if not self._static_loaded:
                await self._load_static(data)
                self._static_loaded = True
            await self._load_volatile(data)
        except StratonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (StratonConnectionError, StratonError) as err:
            raise UpdateFailed(str(err)) from err
        return data

    async def _load_static(self, data: StratonData) -> None:
        """Einmaliger Abruf des statischen Bestands."""
        mapping = {
            "info": "info",
            "version": "version",
            "hostname": "hostname",
            "channels": "channels",
            "colors": "colors",
            "timelines": "timelines",
            "spots": "spots",
            "presettings": "presettings",
            "par-table": "par_table",
        }
        for endpoint in STATIC_ENDPOINTS:
            if (attribute := mapping.get(endpoint)) is None:
                continue
            value = await self.client.async_get(endpoint)
            if value is not None:
                setattr(data, attribute, value)

        data.translations = await self.client.async_get_translations(self._language)

        # Startwerte der Temperaturen einmalig aus der Historie. /api/temperatures
        # liefert rund 78 kB und ist damit für das Polling-Intervall zu schwer
        # (NFR-02); laufende Aktualisierung übernimmt der Socket-Client.
        await self._load_initial_temperatures(data)

    async def _load_initial_temperatures(self, data: StratonData) -> None:
        try:
            history = await self.client.async_get("temperatures")
        except StratonError as err:
            _LOGGER.debug("Temperaturhistorie nicht abrufbar: %s", err)
            return
        if not isinstance(history, list) or not history:
            return
        latest = history[-1]
        for sample in latest.get("data") or ():
            external_id = sample.get("i")
            if not isinstance(external_id, str):
                continue
            data.readings[external_id] = SpotReading(
                external_id=external_id,
                temperature=sample.get("t"),
                online=bool(sample.get("o")),
            )
        data.readings_at = time.monotonic()

    async def _load_volatile(self, data: StratonData) -> None:
        for endpoint in VOLATILE_ENDPOINTS:
            value = await self.client.async_get(endpoint)
            if isinstance(value, dict):
                setattr(data, endpoint.replace("-", "_"), value)
        if data.has_adc:
            current = await self.client.async_get("current")
            if isinstance(current, dict):
                data.current = current

    # ------------------------------------------------------------------ Push

    @property
    def push_connected(self) -> bool:
        """True, solange der Push-Kanal steht."""
        return self.socket is not None and self.socket.connected

    async def async_start_socket(self) -> None:
        """Startet den Push-Kanal und hält ihn dauerhaft am Leben.

        Der Kanal ist die **einzige** Quelle der Temperaturtelemetrie: Die
        Historie unter ``/api/temperatures`` ist mit rund 78 kB zu schwer für
        das Polling-Intervall (NFR-02). Ein abgerissener Socket ließ deshalb
        früher alle Messwerte auf ihrem letzten Stand stehen, ohne dass etwas
        das bemerkt hätte — sichtbar wurde es erst am unplausiblen Wert. Die
        Aufsicht unten baut die Verbindung deshalb selbsttätig wieder auf.
        """
        if self._socket_task is not None:
            return
        self._socket_closing = False
        self._socket_task = self.config_entry.async_create_background_task(
            self.hass, self._async_socket_supervisor(), f"{DOMAIN}-socket"
        )

    async def _async_socket_supervisor(self) -> None:
        """Hält den Push-Kanal offen und verbindet nach einem Abriss neu."""
        delay = SOCKET_RETRY_MIN_SECONDS
        while not self._socket_closing:
            socket = StratonSocketClient(
                self.client.base_url,
                self._async_socket_cookie,
                on_temperatures=self._handle_temperature_spots,
                on_reload=lambda: self.hass.async_create_task(
                    self.async_request_refresh()
                ),
                on_logout=self._handle_logout,
            )
            try:
                await socket.async_connect()
            except Exception as err:  # noqa: BLE001 - Push darf nie hochschlagen
                await socket.async_disconnect()
                # Ein gescheiterter Versuch kann auch an einer serverseitig
                # verworfenen Session liegen; der nächste holt sich deshalb ein
                # frisches Cookie.
                self._relogin_needed = True
                _LOGGER.warning(
                    "Push-Verbindung fehlgeschlagen (%s); nächster Versuch in "
                    "%.0f s. Bis dahin läuft die Integration im Polling-Betrieb, "
                    "ohne Temperaturmesswerte",
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, SOCKET_RETRY_MAX_SECONDS)
                continue

            self.socket = socket
            connected_at = time.monotonic()
            _LOGGER.debug("Push-Kanal steht")
            self.async_update_listeners()

            await socket.async_wait_closed()

            self.socket = None
            await socket.async_disconnect()
            if self._socket_closing:
                break

            # Die Wartezeit nur zurücksetzen, wenn die Verbindung auch wirklich
            # getragen hat. Sonst führte ein Gerät, das die Verbindung sofort
            # wieder fallen lässt, zu einem Dauertakt von Neuversuchen.
            if time.monotonic() - connected_at >= SOCKET_STABLE_SECONDS:
                delay = SOCKET_RETRY_MIN_SECONDS
            _LOGGER.warning(
                "Push-Verbindung zum Gerät abgerissen; baue sie in %.0f s neu auf",
                delay,
            )
            self.async_update_listeners()
            await asyncio.sleep(delay)
            delay = min(delay * 2, SOCKET_RETRY_MAX_SECONDS)

    async def _async_socket_cookie(self) -> str:
        """Liefert das Session-Cookie für einen Verbindungsaufbau.

        Nach einem ``logout`` des Geräts oder einem gescheiterten Versuch wird
        vorher neu angemeldet — ein verworfenes Cookie würde sonst bei jedem
        Wiederverbinden erneut abgelehnt.
        """
        if self._relogin_needed or not self.client.cookies.get("connect.sid"):
            await self.client.async_login()
            self._relogin_needed = False
        return self.client.cookies.get("connect.sid", "")

    async def async_stop_socket(self) -> None:
        """Beendet den Push-Kanal und verlässt zuvor den Preview-Modus (NFR-10)."""
        self._socket_closing = True

        if (task := self._socket_task) is not None:
            self._socket_task = None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if (socket := self.socket) is not None:
            self.socket = None
            await socket.async_disconnect()

    def _handle_logout(self) -> None:
        """Das Gerät hat die Session beendet.

        Der bestehende Socket ist damit wertlos. Er wird geschlossen, worauf die
        Aufsicht ihn mit einer frischen Anmeldung neu aufbaut.
        """
        self._relogin_needed = True
        if (socket := self.socket) is not None:
            self.hass.async_create_task(socket.async_disconnect())
        self.hass.async_create_task(self.async_request_refresh())

    def _handle_temperature_spots(self, payload: Any) -> None:
        """Verarbeitet ``temperature-spots``: rund alle zwei Sekunden.

        Die Auswertung läuft bei jedem Event, damit der Wächter schnell reagiert.
        Die Weitergabe an Home Assistant wird gedrosselt, weil ein Zwei-Sekunden-
        Takt sonst die Zustandsmaschine und die Recorder-Datenbank flutet.
        """
        if not isinstance(payload, list):
            _LOGGER.debug("temperature-spots mit unerwarteter Nutzlast: %r", payload)
            return

        data = self.data
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            external_id = entry.get("externalId")
            if not isinstance(external_id, str):
                continue
            raw = entry.get("rawtemperature")
            data.readings[external_id] = SpotReading(
                external_id=external_id,
                temperature=entry.get("temperature"),
                raw_temperature=raw if isinstance(raw, list) else [],
                online=entry.get("online"),
            )
        data.readings_at = time.monotonic()

        decision = self.guardian.evaluate(data.max_temperature, time.monotonic())
        if decision.changed:
            self.hass.async_create_task(self._async_apply_guardian(decision))

        now = time.monotonic()
        if decision.changed or now - self._last_push >= PUSH_THROTTLE_SECONDS:
            self._last_push = now
            self.async_set_updated_data(data)

    # -------------------------------------------------------- Schreibpfad

    async def _async_write_timelines(self, timelines: list[dict[str, Any]]) -> None:
        """Schreibt Timelines über ``PUT /api/data``.

        Vollersetzung des gesamten Dokuments — die Geräte-API kennt nichts
        Granulareres. ``spots`` und ``colors`` werden unverändert aus dem
        zwischengespeicherten Zustand mitgesendet.
        """
        response = await self.client.async_put(
            "data",
            {
                "timelines": timelines,
                "spots": self.data.spots,
                "colors": self.data.colors,
            },
        )
        # Bestätigte Asymmetrie der Geräte-API: Request sendet "timelines",
        # Response liefert "lines".
        if isinstance(response, dict):
            if isinstance(lines := response.get("lines"), list):
                self.data.timelines = lines
            if isinstance(spots := response.get("spots"), list):
                self.data.spots = spots
            if isinstance(colors := response.get("colors"), list):
                self.data.colors = colors
        self.async_update_listeners()

    async def _async_backup(self, timelines: list[dict[str, Any]]) -> None:
        """Sichert den Stand vor jedem Schreibvorgang (FR-12)."""
        await self._backup_store.async_save(
            {
                "saved_at": dt_util.utcnow().isoformat(),
                "timelines": timelines,
            }
        )

    async def async_set_intensity(self, intensity: float) -> None:
        """Setzt die globale Intensität in der Semantik des Geräte-Reglers."""
        ceiling = min(self._max_intensity, MAX_INTENSITY)
        if intensity > ceiling:
            _LOGGER.warning(
                "Intensität %.1f überschreitet die Obergrenze %.1f und wurde geklemmt",
                intensity,
                ceiling,
            )
            intensity = ceiling

        async with self._write_lock:
            if self._guard_snapshot is not None:
                raise HomeAssistantError(
                    "Der Temperaturwächter regelt gerade; Intensität nicht änderbar"
                )
            current = self.data.timelines
            await self._async_backup(current)
            await self._async_write_timelines(scaled_timelines(current, intensity))
            self._manual_intensity = True
            _LOGGER.info("Intensität auf %.1f gesetzt", intensity)

    # ----------------------------------------------------------- Ein/Aus

    @property
    def is_on(self) -> bool:
        """True, sobald der Tagesverlauf irgendwo über 0 liegt."""
        return current_intensity(self.data.timelines) > 0

    @property
    def intensity_before_off(self) -> float | None:
        """Zuletzt vor dem Ausschalten aktive Intensität."""
        return self._intensity_before_off

    def restore_intensity_before_off(self, value: float | None) -> None:
        """Übernimmt den gemerkten Wert nach einem Neustart."""
        if value is not None and MIN_INTENSITY < value <= MAX_INTENSITY:
            self._intensity_before_off = value

    async def async_turn_off(self) -> None:
        """Schaltet die Leuchte aus, indem die Intensität auf 0 gesetzt wird.

        Die vorherige Intensität wird gemerkt, damit das Einschalten sie
        wiederherstellen kann. Der Tagesverlauf selbst bleibt erhalten — nur
        skaliert auf 0.
        """
        current = current_intensity(self.data.timelines)
        if current > 0:
            self._intensity_before_off = current
        await self.async_set_intensity(MIN_INTENSITY)

    async def async_turn_on(self) -> None:
        """Stellt die Intensität wieder her, die vor dem Ausschalten galt.

        Ohne gemerkten Wert — etwa weil die Leuchte schon vor der Einrichtung
        aus war — wird auf den Bezugswert des Profils zurückgegangen, also auf
        das, was das Programm als volle Helligkeit vorsieht.
        """
        target = self._intensity_before_off
        if target is None:
            target = max_value_org(self.data.timelines) or MAX_INTENSITY
            _LOGGER.debug(
                "Kein gemerkter Wert vorhanden, schalte auf den Profilbezug %.1f",
                target,
            )
        await self.async_set_intensity(min(target, MAX_INTENSITY))

    # ------------------------------------------------------ Programme/Farben

    @property
    def programs(self) -> list[Program]:
        """Verfügbare Lichtprogramme mit aufgelösten Bezeichnungen."""
        return parse_programs(self.data.presettings, self.data.translations)

    @property
    def colors(self) -> list[ColorPreset]:
        """Verfügbare Farben mit ihrer Zusammensetzung."""
        return parse_colors(self.data.colors, self.data.translations)

    @property
    def intensity_now(self) -> float | None:
        """Intensität, die der Tagesverlauf **gerade jetzt** vorgibt.

        Nicht zu verwechseln mit der Reglerstellung, die den Tagesspitzenwert
        angibt. Grundlage ist die Gerätezeit aus ``/api/timeinfo``, damit eine
        abweichende Uhr am Gerät nicht zu falschen Werten führt.
        """
        timestamp = self.data.timeinfo.get("ts")
        if not isinstance(timestamp, (int, float)):
            return None
        local = dt_util.utc_from_timestamp(timestamp / 1000)
        offset = self.data.timeinfo.get("offset")
        if isinstance(offset, (int, float)):
            # getTimezoneOffset liefert Minuten mit umgekehrtem Vorzeichen.
            local -= timedelta(minutes=offset)
        seconds = local.hour * 3600 + local.minute * 60 + local.second
        return intensity_at(self.data.timelines, seconds)

    @property
    def schedule_colors(self) -> list[ColorPreset]:
        """Farben, die der aktuelle Tagesverlauf verwendet."""
        return colors_in_schedule(self.data.timelines, self.data.translations)

    @property
    def schedule(self) -> list[dict[str, Any]]:
        """Tagesverlauf als Liste aus Uhrzeit, Intensität und Farbe."""
        return schedule_overview(self.data.timelines)

    def find_program(self, label: str) -> Program | None:
        return next((p for p in self.programs if p.label == label), None)

    @property
    def active_program(self) -> Program | None:
        """Das laut Timeline hinterlegte Programm."""
        for timeline in self.data.timelines:
            presetting = timeline.get("presetting") or {}
            if (program_id := presetting.get("id")) is not None:
                return next((p for p in self.programs if p.id == program_id), None)
        return None

    async def async_load_program(self, label: str, level_index: int) -> None:
        """Lädt ein Lichtprogramm mit der gewählten Gewöhnungsstufe.

        Zweistufig, wie die Geräteoberfläche: ``POST /load-presettings`` liefert
        den neuen Tagesverlauf, ``PUT /api/data`` schreibt ihn fest.
        """
        program = self.find_program(label)
        if program is None:
            raise HomeAssistantError(f"Unbekanntes Programm: {label}")

        group_ids = [
            timeline["_id"] for timeline in self.data.timelines if "_id" in timeline
        ]
        # Das gewohnte Lichtfenster beibehalten, statt es vom Programm
        # überschreiben zu lassen — so verfährt auch die Geräteoberfläche.
        timerange = derive_timerange(program, self.data.timelines)
        payload = build_load_payload(
            program, level_index, group_ids, timerange=timerange
        )

        async with self._write_lock:
            if self._guard_snapshot is not None:
                raise HomeAssistantError(
                    "Der Temperaturwächter regelt gerade; Programm nicht änderbar"
                )
            await self._async_backup(self.data.timelines)
            loaded = await self.client.async_post_root("load-presettings", payload)
            timelines = self._timelines_from(loaded)
            if timelines is None:
                raise HomeAssistantError(
                    "Gerät lieferte keinen verwertbaren Tagesverlauf zurück"
                )
            await self._async_write_timelines(timelines)
            self._manual_intensity = False
            _LOGGER.info(
                "Programm %r mit Stufe %r geladen",
                program.label,
                program.levels[level_index].title,
            )

    @staticmethod
    def _timelines_from(response: Any) -> list[dict[str, Any]] | None:
        """Holt die Timelines aus einer Antwort; das Gerät nennt sie mal ``lines``."""
        if isinstance(response, list) and response:
            return response
        if isinstance(response, dict):
            for key in ("timelines", "lines"):
                if isinstance(value := response.get(key), list) and value:
                    return value
        return None

    # ------------------------------------------------------- Farb-Editor

    @property
    def edited_color(self) -> ColorPreset | None:
        """Farbe, die gerade zur Bearbeitung ausgewählt ist."""
        if self._color_edit_id is None:
            return next(iter(self.colors), None)
        return next((c for c in self.colors if c.id == self._color_edit_id), None)

    @property
    def color_buffer(self) -> dict[str, int]:
        """Bearbeitungsstand der ausgewählten Farbe.

        Die Regler schreiben hierhin, **nicht** zum Gerät. Erst der
        Speichern-Knopf überträgt. Ohne diesen Puffer erzeugte jede
        Reglerbewegung einen Flash-Schreibvorgang.
        """
        if not self._color_buffer and (color := self.edited_color) is not None:
            self._color_buffer = dict(color.composition)
        return self._color_buffer

    @property
    def color_buffer_dirty(self) -> bool:
        """True, wenn der Puffer vom Gerätestand abweicht."""
        color = self.edited_color
        return color is not None and self.color_buffer != color.composition

    def select_color_for_edit(self, name: str) -> None:
        """Wählt eine Farbe aus und verwirft einen offenen Bearbeitungsstand."""
        color = next((c for c in self.colors if c.name == name), None)
        if color is None:
            raise HomeAssistantError(f"Unbekannte Farbe: {name}")
        self._color_edit_id = color.id
        self._color_buffer = dict(color.composition)
        self.async_update_listeners()

    def set_buffered_channel(self, channel: str, value: int) -> None:
        """Ändert einen Kanal im Puffer, ohne zum Gerät zu schreiben."""
        buffer = self.color_buffer
        if channel not in buffer:
            raise HomeAssistantError(
                f"Kanal {channel!r} gehört nicht zu dieser Farbe; "
                f"vorhanden sind {sorted(buffer)}"
            )
        buffer[channel] = int(value)
        self.async_update_listeners()

    def discard_color_buffer(self) -> None:
        """Verwirft den Bearbeitungsstand und lädt die Gerätewerte zurück."""
        self._color_buffer = {}
        self.async_update_listeners()

    async def async_save_color_buffer(self) -> None:
        """Überträgt den Bearbeitungsstand zum Gerät."""
        color = self.edited_color
        if color is None:
            raise HomeAssistantError("Keine Farbe ausgewählt")
        if not self.color_buffer_dirty:
            _LOGGER.debug("Farbe %s unverändert, kein Schreibvorgang", color.name)
            return
        await self.async_set_color(color.id, dict(self.color_buffer))
        self._color_buffer = {}

    async def async_set_color(self, color_id: int, values: dict[str, int]) -> None:
        """Ändert die Zusammensetzung einer Farbe.

        Wertebereich 0–255 je Kanal, geprüft in :func:`with_updated_color`.
        """
        async with self._write_lock:
            if self._guard_snapshot is not None:
                raise HomeAssistantError(
                    "Der Temperaturwächter regelt gerade; Farben nicht änderbar"
                )
            await self._async_backup(self.data.timelines)
            updated = with_updated_color(self.data.colors, color_id, values)
            response = await self.client.async_put(
                "data",
                {
                    "timelines": self.data.timelines,
                    "spots": self.data.spots,
                    "colors": updated,
                },
            )
            if isinstance(response, dict):
                if isinstance(lines := response.get("lines"), list):
                    self.data.timelines = lines
                if isinstance(colors := response.get("colors"), list):
                    self.data.colors = colors
            self.async_update_listeners()
            _LOGGER.info("Farbe %s geändert: %s", color_id, values)

    # -------------------------------------------------------------- Wächter

    def update_guardian_config(self, **changes: Any) -> None:
        """Übernimmt geänderte Wächter-Parameter aus den Entitäten."""
        self.guardian.update_config(**changes)
        decision = self.guardian.evaluate(self.data.max_temperature, time.monotonic())
        self.hass.async_create_task(self._async_apply_guardian(decision))
        self.async_update_listeners()

    async def _async_apply_guardian(self, decision: GuardianDecision) -> None:
        """Überträgt die Entscheidung des Wächters auf das Gerät.

        Gesendet werden ausschließlich Werte, die aus einem Schnappschuss des
        Ist-Zustands mit einem Faktor ≤ 1 hervorgehen. Der Wächter kann die
        Leuchte damit nie aufhellen, und die Freigabe stellt den Schnappschuss
        wortgetreu wieder her.
        """
        async with self._write_lock:
            try:
                if decision.engaged:
                    await self._async_engage(decision)
                elif self._guard_snapshot is not None:
                    await self._async_release()
            except (StratonError, IntensityError) as err:
                _LOGGER.error("Wächter konnte nicht eingreifen: %s", err)

    async def _async_engage(self, decision: GuardianDecision) -> None:
        if self._guard_snapshot is None:
            snapshot = copy.deepcopy(self.data.timelines)
            if not any(v > 0 for v in node_values(snapshot)):
                _LOGGER.debug("Wächter greift nicht ein: Kurve steht bereits auf 0")
                return
            self._guard_snapshot = snapshot
            # Vor dem ersten Eingriff persistieren, damit ein Absturz die
            # abgesenkte Kurve nicht dauerhaft stehen lässt.
            await self._backup_store.async_save(
                {
                    "saved_at": dt_util.utcnow().isoformat(),
                    "timelines": snapshot,
                    "guard_active": True,
                }
            )
            _LOGGER.info(
                "Wächter greift ein: Spitzenintensität %.1f, Reduktion %.0f %%",
                current_intensity(snapshot),
                decision.level,
            )

        reduced = rescaled_by_factor(self._guard_snapshot, decision.factor)
        await self._async_write_timelines(reduced)

    async def _async_release(self) -> None:
        """Stellt den Schnappschuss exakt wieder her."""
        snapshot = self._guard_snapshot
        self._guard_snapshot = None
        if snapshot is None:
            return
        await self._async_write_timelines(snapshot)
        await self._backup_store.async_remove()
        _LOGGER.info("Wächter beendet, ursprünglicher Tagesverlauf wiederhergestellt")

    async def async_recover_snapshot(self) -> None:
        """Stellt nach einem Neustart eine hängengebliebene Absenkung zurück.

        Bricht Home Assistant ab, während der Wächter regelt, bleibt die
        abgesenkte Kurve im Gerät stehen. Beim nächsten Start wird sie hier
        anhand des persistierten Schnappschusses zurückgeschrieben.
        """
        stored = await self._backup_store.async_load()
        if not isinstance(stored, dict) or not stored.get("guard_active"):
            return
        timelines = stored.get("timelines")
        if not isinstance(timelines, list) or not timelines:
            return
        _LOGGER.warning(
            "Unterbrochener Wächter-Eingriff gefunden (vom %s); stelle den "
            "ursprünglichen Tagesverlauf wieder her",
            stored.get("saved_at", "unbekannt"),
        )
        async with self._write_lock:
            await self._async_write_timelines(timelines)
            await self._backup_store.async_remove()
