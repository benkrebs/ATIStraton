"""Die ATI Straton Integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import StratonApiClient
from .const import CONF_MAX_INTENSITY, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import StratonCoordinator
from .intensity import MAX_INTENSITY
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type StratonConfigEntry = ConfigEntry[StratonCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: StratonConfigEntry) -> bool:
    """Richtet einen Geräteeintrag ein."""
    client = StratonApiClient(
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session=async_create_clientsession(hass),
    )

    coordinator = StratonCoordinator(
        hass,
        entry,
        client,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        max_intensity=entry.options.get(CONF_MAX_INTENSITY, MAX_INTENSITY),
    )
    await coordinator.async_config_entry_first_refresh()
    # Vor allem anderen: eine durch einen Absturz stehengebliebene Absenkung
    # des Wächters zurücknehmen.
    await coordinator.async_recover_snapshot()
    await coordinator.async_start_socket()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # Auch beim Herunterfahren von Home Assistant muss der Preview-Modus sauber
    # verlassen werden, sonst bliebe die Leuchte in einem Override stehen (NFR-10).
    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            lambda _event: hass.async_create_task(coordinator.async_stop_socket()),
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StratonConfigEntry) -> bool:
    """Entlädt den Eintrag und gibt die Regelung frei.

    Das Verlassen des Preview-Modus ist zwingend (NFR-10): sonst bliebe das
    Aquarium in einem Override hängen, wenn die Integration entladen wird.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data
        await coordinator.async_stop_socket()
        await coordinator.client.async_close()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: StratonConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
