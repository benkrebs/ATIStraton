"""Diagnosedaten der ATI Straton Integration.

Die Endpunkte ``/api/user`` und ``/api/network`` liefern Geheimnisse im Klartext
(Sicherheitsbefunde S-01/S-04). Sie werden vom API-Client bereits gesperrt und
tauchen deshalb hier gar nicht erst auf — die Redaktion unten schützt zusätzlich
gegen künftige Änderungen.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import StratonConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_HOST, "password", "key", "psk", "ssid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StratonConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "limits": {
            "ceilings": dict(coordinator.limits.ceilings),
            "safety_factor": coordinator.limits.safety_factor,
            "effective": {
                channel: coordinator.limits.effective_ceiling(channel)
                for channel in coordinator.limits.channels
            },
        },
        "device": async_redact_data(
            {
                "info": data.info,
                "version": data.version,
                "status": data.status,
                "current": data.current,
                "timeinfo": data.timeinfo,
                "spots": data.spots,
                "timelines": data.timelines,
                "readings": {k: asdict(v) for k, v in data.readings.items()},
            },
            TO_REDACT,
        ),
    }
