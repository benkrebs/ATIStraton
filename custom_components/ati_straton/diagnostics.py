"""Diagnosedaten der ATI Straton Integration.

Die Endpunkte ``/api/user`` und ``/api/network`` liefern sensible Daten. Sie
werden vom API-Client bereits gesperrt und tauchen deshalb hier gar nicht erst
auf — die Redaktion unten schützt zusätzlich gegen künftige Änderungen.
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
        "mode": coordinator.mode.value,
        "push": {
            "connected": coordinator.push_connected,
            "telemetry_age": data.telemetry_age,
            "telemetry_stale": data.telemetry_stale,
            "seconds_since_last_message": (
                coordinator.socket.seconds_since_last_message
                if coordinator.socket is not None
                else None
            ),
        },
        "guard": {
            "engaged": coordinator.guard_engaged,
            "state": coordinator.guardian.state.value,
            "reduction": coordinator.guardian.level,
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
