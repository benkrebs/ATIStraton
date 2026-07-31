"""Gemeinsame Basis aller Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StratonCoordinator


class StratonEntity(CoordinatorEntity[StratonCoordinator]):
    """Bindet alle Entitäten an ein gemeinsames Gerät."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StratonCoordinator, key: str) -> None:
        super().__init__(coordinator)
        data = coordinator.data
        self._attr_unique_id = f"{data.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.device_id)},
            manufacturer="ATI",
            model=data.info.get("deviceType"),
            name=data.hostname.get("hostname") or "ATI Straton",
            sw_version=data.version.get("number"),
            configuration_url=coordinator.client.base_url,
        )
