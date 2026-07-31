"""Push-Anbindung an das Gerät — ausschließlich empfangend.

Der Socket war ursprünglich auch als Steuerweg vorgesehen
(``color-preview``/``color-change``). Am Gerät verifiziert: **diese Ereignisse
bleiben wirkungslos**, auch mit protokollkonformem Engine.IO-3-Client. Gesteuert
wird deshalb über :mod:`.intensity` und ``PUT /api/data``.

Der Socket liefert weiterhin die Temperaturtelemetrie, und zwar etwa alle zwei
Sekunden. Die Auswertung läuft bei jedem Ereignis, weil der Temperaturwächter
davon abhängt; die Weitergabe an Home Assistant wird gedrosselt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import (
    EVENT_CHANGED_INTENSITY,
    EVENT_INTENSITY_AUTO_CORRECTION,
    EVENT_LOGOUT,
    EVENT_NEW_SPOTS,
    EVENT_TEMPERATURE_SPOTS,
)
from .eio3 import SocketIO2Client, SocketIO2Error

_LOGGER = logging.getLogger(__name__)


class StratonSocketClient:
    """Empfängt Telemetrie und Statusereignisse des Geräts."""

    def __init__(
        self,
        base_url: str,
        cookies: dict[str, str],
        *,
        on_temperatures: Callable[[Any], None],
        on_reload: Callable[[], None],
        on_logout: Callable[[], None],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._cookies = cookies
        self._on_reload = on_reload
        self._on_logout = on_logout
        self._client = SocketIO2Client(base_url, session=session)

        self._client.on(EVENT_TEMPERATURE_SPOTS, on_temperatures)
        self._client.on(EVENT_NEW_SPOTS, self._handle_new_spots)
        self._client.on(EVENT_LOGOUT, self._handle_logout)
        self._client.on(EVENT_CHANGED_INTENSITY, self._handle_changed_intensity)
        self._client.on(EVENT_INTENSITY_AUTO_CORRECTION, self._handle_auto_correction)

    @property
    def connected(self) -> bool:
        return self._client.connected

    async def async_connect(self) -> None:
        cookie = self._cookies.get("connect.sid")
        if not cookie:
            raise SocketIO2Error("Kein Session-Cookie für die Socket-Verbindung")
        await self._client.async_connect(f"connect.sid={cookie}")

    async def async_disconnect(self) -> None:
        await self._client.async_disconnect()

    def _handle_new_spots(self, *_: Any) -> None:
        _LOGGER.debug("new-spots empfangen, fordere Vollreload an")
        self._on_reload()

    def _handle_logout(self, *_: Any) -> None:
        _LOGGER.warning("Gerät hat die Session beendet")
        self._on_logout()

    @staticmethod
    def _handle_changed_intensity(*args: Any) -> None:
        _LOGGER.debug("changed-intensity: %s", args)

    @staticmethod
    def _handle_auto_correction(*args: Any) -> None:
        # Das Gerät regelt oberhalb von info.maxTemperature selbst nach. Auf
        # Info-Level, weil sich das mit dem eigenen Wächter überlagern kann.
        _LOGGER.info("Gerät meldet intensity-auto-correction: %s", args)
