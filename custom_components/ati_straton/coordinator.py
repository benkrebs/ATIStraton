"""Zustandsverwaltung der ATI Straton Integration."""

from __future__ import annotations

import asyncio
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
    STATIC_ENDPOINTS,
    STORAGE_VERSION,
    VOLATILE_ENDPOINTS,
)
from .guardian import GuardianConfig, GuardianDecision, TemperatureGuardian
from .intensity import (
    MAX_INTENSITY,
    IntensityError,
    current_intensity,
    node_values,
    rescaled_by_factor,
    scaled_timelines,
)
from .limits import ChannelLimits
from .programs import (
    ColorPreset,
    Program,
    build_load_payload,
    derive_timerange,
    parse_colors,
    parse_programs,
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
    def channel_values(self) -> dict[str, int]:
        """Aktuelle Kanalwerte aus ``/api/status``."""
        return {
            channel["name"]: channel.get("value", 0)
            for channel in self.status.get("channels") or ()
            if isinstance(channel.get("name"), str)
        }

    @property
    def preview_active(self) -> bool:
        return bool(self.status.get("isColorPreview"))

    @property
    def max_temperature(self) -> float | None:
        """Höchste aktuell gemeldete Spot-Temperatur — Eingang des Wächters."""
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

    def external_ids(self) -> list[str]:
        """``<deviceId>:<spot._id>`` für alle Spots."""
        return [
            f"{self.device_id}:{spot['_id']}" for spot in self.spots if "_id" in spot
        ]


class StratonCoordinator(DataUpdateCoordinator[StratonData]):
    """Pollt das Gerät und nimmt Push-Updates des Socket-Clients entgegen."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: StratonApiClient,
        scan_interval: int,
        safety_factor: float,
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
        self._safety_factor = safety_factor
        self._static_loaded = False
        self.limits = ChannelLimits()
        self.data = StratonData()

        self.socket: StratonSocketClient | None = None
        self.guardian = TemperatureGuardian(guardian_config)
        self._max_intensity = max_intensity
        # Exakter Stand vor dem Eingriff des Wächters; dient zugleich als
        # Merkmal dafür, dass eine Absenkung aktiv ist.
        self._guard_snapshot: list[dict[str, Any]] | None = None
        # Wird gesetzt, sobald die Intensität über die Integration verändert
        # wurde, und beim Laden eines Programms wieder zurückgenommen.
        self._manual_intensity = False
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
        """Einmaliger Abruf des statischen Bestands plus Ableitung der Grenzen."""
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

        self.limits = ChannelLimits.from_device(
            data.spots, data.colors, self._safety_factor
        )
        _LOGGER.debug("Abgeleitete Kanalgrenzen: %s", dict(self.limits.ceilings))

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

    async def async_start_socket(self) -> None:
        """Startet den Push-Kanal. Ein Fehlschlag degradiert auf reines Polling."""
        socket = StratonSocketClient(
            self.client.base_url,
            self.client.cookies,
            on_temperatures=self._handle_temperature_spots,
            on_reload=lambda: self.hass.async_create_task(self.async_request_refresh()),
            on_logout=self._handle_logout,
        )
        try:
            await socket.async_connect()
        except Exception as err:  # noqa: BLE001 - Push ist optional
            _LOGGER.warning(
                "Socket-Verbindung fehlgeschlagen (%s); Integration läuft im "
                "Polling-Betrieb weiter",
                err,
            )
            return
        self.socket = socket

    async def async_stop_socket(self) -> None:
        """Beendet den Push-Kanal und verlässt zuvor den Preview-Modus (NFR-10)."""
        if self.socket is None:
            return
        try:
            await self.socket.async_disconnect()
        finally:
            self.socket = None

    def _handle_logout(self) -> None:
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

    # ------------------------------------------------------ Programme/Farben

    @property
    def programs(self) -> list[Program]:
        """Verfügbare Lichtprogramme mit aufgelösten Bezeichnungen."""
        return parse_programs(self.data.presettings, self.data.translations)

    @property
    def colors(self) -> list[ColorPreset]:
        """Verfügbare Farben mit ihrer Zusammensetzung."""
        return parse_colors(self.data.colors, self.data.translations)

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
            loaded = await self.client.async_post("load-presettings", payload)
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
