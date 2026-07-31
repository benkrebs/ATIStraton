"""Dienste der ATI Straton Integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .programs import MAX_CHANNEL_VALUE, MIN_CHANNEL_VALUE, ProgramError

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_COLOR = "set_color"

ATTR_COLOR = "color"
ATTR_VALUES = "values"

SET_COLOR_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required(ATTR_COLOR): vol.Any(cv.string, vol.Coerce(int)),
        vol.Required(ATTR_VALUES): vol.Schema(
            {
                cv.string: vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_CHANNEL_VALUE, max=MAX_CHANNEL_VALUE),
                )
            }
        ),
    }
)


def _coordinator_for(hass: HomeAssistant, device_id: str) -> Any:
    """Ermittelt den Coordinator zu einer Geräte-ID."""
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unbekanntes Gerät: {device_id}")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return entry.runtime_data
    raise HomeAssistantError(f"Gerät {device_id} gehört nicht zu {DOMAIN}")


async def _async_set_color(call: ServiceCall) -> None:
    coordinator = _coordinator_for(call.hass, call.data["device_id"])
    requested = call.data[ATTR_COLOR]

    color = next(
        (
            c
            for c in coordinator.colors
            if c.id == requested or c.name == str(requested)
        ),
        None,
    )
    if color is None:
        available = ", ".join(f"{c.name!r} (id={c.id})" for c in coordinator.colors)
        raise HomeAssistantError(
            f"Farbe {requested!r} nicht gefunden. Verfügbar: {available}"
        )

    try:
        await coordinator.async_set_color(color.id, dict(call.data[ATTR_VALUES]))
    except ProgramError as err:
        raise HomeAssistantError(str(err)) from err


def async_register_services(hass: HomeAssistant) -> None:
    """Registriert die Dienste einmalig pro Home-Assistant-Instanz."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_COLOR):
        return
    hass.services.async_register(
        DOMAIN, SERVICE_SET_COLOR, _async_set_color, schema=SET_COLOR_SCHEMA
    )
