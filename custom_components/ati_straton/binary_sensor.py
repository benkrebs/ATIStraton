"""Binärsensoren der ATI Straton Integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data

    entities: list[BinarySensorEntity] = [
        StratonSpotOnlineSensor(coordinator, spot)
        for spot in data.spots
        if "_id" in spot
    ]

    if data.has_adc:
        entities.append(StratonCurrentWarningSensor(coordinator))

    async_add_entities(entities)


class StratonSpotOnlineSensor(StratonEntity, BinarySensorEntity):
    """Erreichbarkeit eines physischen LED-Moduls."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StratonCoordinator, spot: dict[str, Any]) -> None:
        spot_id = spot["_id"]
        super().__init__(coordinator, f"spot_{spot_id}_online")
        self._external_id = f"{coordinator.data.device_id}:{spot_id}"
        self._attr_name = f"{spot.get('name') or f'Spot {spot_id}'} Verbindung"

    @property
    def is_on(self) -> bool | None:
        reading = self.coordinator.data.readings.get(self._external_id)
        return None if reading is None else reading.online

    @property
    def available(self) -> bool:
        """Ohne aktuelle Telemetrie ist über die Erreichbarkeit nichts bekannt."""
        return super().available and not self.coordinator.data.telemetry_stale


class StratonCurrentWarningSensor(StratonEntity, BinarySensorEntity):
    """Warn- und Gefahrenschwelle der Stromaufnahme."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Stromwarnung"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "current_warning")

    @property
    def is_on(self) -> bool:
        current = self.coordinator.data.current
        return bool(current.get("isWarning") or current.get("isDanger"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self.coordinator.data.current
        return {
            "is_warning": current.get("isWarning"),
            "is_danger": current.get("isDanger"),
            "warn_threshold": current.get("warn"),
            "max_threshold": current.get("max"),
        }
